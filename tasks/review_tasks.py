import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from reviews.models import Review, Comment
from github_client.gh_client import fetch_pr_diff_chunks
from analyzer.llm_client import analyze_chunk, OpenRouterAPIError
from github_client.comment_poster import post_github_review

logger = logging.getLogger(__name__)

MAX_COMMENTS = 15
MAX_PARALLEL_WORKERS = 4  # Production Tuning: Allows concurrent asset queries


@shared_task(bind=True, max_retries=3)
def process_pr_review(self, review_id, repo_full_name, pr_number, installation_id, head_sha):
    try:
        # Atomic selection phase protects operations across multi-node workers
        with transaction.atomic():
            try:
                review = Review.objects.select_for_update().get(id=review_id)
            except Review.DoesNotExist:
                logger.error(f"Termination: Review record tracking {review_id} missing from database.")
                return

        review.status = Review.Status.RUNNING
        review.save(update_fields=["status"])

        # Step 1: Collect tracking patch streams 
        chunks = fetch_pr_diff_chunks(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            installation_id=installation_id,
        )

        if not chunks:
            _mark_complete(review, score=100)
            return

        # Step 2: High-Speed Concurrent File Processing Pipeline
        all_issues = []
        valid_chunks = [c for c in chunks if not c["skipped"]]
        llm_failures = []

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
            future_to_chunk = {
                executor.submit(
                    analyze_chunk, 
                    filename=chunk["filename"], 
                    language=chunk["language"], 
                    patch=chunk["patch"]
                ): chunk for chunk in valid_chunks
            }

            for future in as_completed(future_to_chunk):
                chunk_meta = future_to_chunk[future]
                try:
                    issues = future.result()
                    if issues:
                        all_issues.extend(issues)
                except OpenRouterAPIError as exc:
                    logger.error("OpenRouter rejected chunk %s: %s", chunk_meta["filename"], exc)
                    llm_failures.append(str(exc))
                    if exc.status_code in (400, 401, 402, 403):
                        raise
                except Exception as exc:
                    logger.error("Failed analyzing %s: %s", chunk_meta["filename"], exc)
                    llm_failures.append(f"{chunk_meta['filename']}: {exc}")

        if llm_failures and not all_issues:
            raise RuntimeError("LLM analysis failed for all files. First error: " + llm_failures[0])

        # Step 3: Noise Filters and Capacity Limits
        if len(chunks) > 20:
            all_issues = [i for i in all_issues if i["severity"] in ("CRITICAL", "WARNING")]

        all_issues = all_issues[:MAX_COMMENTS]

        # Step 4: Batch Database Inserts
        comments_to_create = [
            Comment(
                review=review,
                file_path=issue["file"],
                line_start=issue["line_start"],
                line_end=issue["line_end"],
                severity=issue["severity"].lower(),
                category=issue.get("category", "dsa").lower(),
                issue=issue["issue"],
                suggestion=issue["suggestion"],
                time_complexity_before=issue.get("complexity_before", ""),
                time_complexity_after=issue.get("complexity_after", ""),
            ) for issue in all_issues
        ]
        
        with transaction.atomic():
            Comment.objects.filter(review=review).delete()  # Safeguard against duplicated retries
            Comment.objects.bulk_create(comments_to_create)

        # Step 5: Compute metrics and close operations
        score = compute_health_score(all_issues)
        _mark_complete(review, score=score)

        logger.info(f"Review {review_id} complete — Issues logged: {len(all_issues)}, Computed Score: {score}")

        # Step 6: Post inline comments to GitHub via direct memory variables
        post_github_review(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            installation_id=installation_id,
            head_sha=head_sha,
            comments=comments_to_create,  # Safely avoids database cache race condition
            health_score=score,
        )

    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.warning(f"Transient fault inside review worker {review_id}. Retrying task... Error: {exc}")
            raise self.retry(exc=exc, countdown=2 ** self.request.retries)

        logger.critical(f"Review tracking task {review_id} conclusively failed: {exc}")
        try:
            review = Review.objects.get(id=review_id)
            review.status = Review.Status.FAILED
            review.error_message = str(exc)
            review.save(update_fields=["status", "error_message"])
        except Review.DoesNotExist:
            pass
        raise exc


def compute_health_score(issues: list[dict]) -> int:
    deductions = {"CRITICAL": 20, "WARNING": 8, "SUGGESTION": 2}
    total = sum(deductions.get(str(i["severity"]).upper(), 0) for i in issues)
    return max(0, 100 - total)


def _mark_complete(review, score: int):
    review.status = Review.Status.COMPLETED
    review.health_score = score
    review.completed_at = timezone.now()
    review.save(update_fields=["status", "health_score", "completed_at"])