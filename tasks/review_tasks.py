import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from reviews.models import Review, Comment
from github_client.gh_client import fetch_pr_diff_chunks
from analyzer.llm_client import (
    analyze_chunk,
    CHUNK_ERROR_PARSE_FAILED,
    CHUNK_ERROR_PROVIDER_EXHAUSTED,
    CHUNK_ERROR_SERVICE_UNAVAILABLE,
)
from github_client.status_poster import (
    post_commit_status,
    post_commit_status_unavailable,
)
from github_client.comment_poster import post_unavailable_review_comment
from analyzer.repo_config import fetch_repo_config, should_skip_path

logger = logging.getLogger(__name__)

MAX_COMMENTS = 15
MAX_PARALLEL_WORKERS = 4  


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
        # ── Fetch repo config (.prizmareview.yml or defaults) ──────────────────
        config = fetch_repo_config(
            repo_full_name=repo_full_name,
            installation_id=installation_id,
            head_sha=head_sha,
        )
        logger.info(
            f"Config loaded — threshold={config.fail_threshold} "
            f"max_comments={config.max_comments} "
            f"skip_categories={config.skip_categories}"
        )

        # Step 2: High-Speed Concurrent File Processing Pipeline
        
        chunks = [
            c for c in chunks
            if not should_skip_path(c["filename"], config.skip_paths)
        ]

        if config.language_focus:
            chunks = [
                c for c in chunks
                if c["language"] in config.language_focus or c.get("skipped", False)
            ]

        # NOW build the valid_chunks list from the clean, filtered chunks list
        all_issues = []
        valid_chunks = [c for c in chunks if not c.get("skipped", False)]
        llm_failures = []
        chunk_errors = []  # per-file analysis failures (parse, provider, etc.)

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
            future_to_chunk = {
                executor.submit(
                    analyze_chunk,
                    filename=chunk["filename"],
                    language=chunk["language"],
                    patch=chunk.get("patch", ""),
                ): chunk for chunk in valid_chunks
            }

            for future in as_completed(future_to_chunk):
                chunk_meta = future_to_chunk[future]
                try:
                    issues, chunk_error = future.result()
                    if chunk_error:
                        chunk_errors.append((chunk_meta["filename"], chunk_error))
                        logger.warning(
                            "Chunk analysis error for %s: %s",
                            chunk_meta["filename"],
                            chunk_error,
                        )
                    if issues:
                        all_issues.extend(issues)
                except Exception as exc:
                    error_msg = str(exc)
                    status_code = getattr(exc, "status_code", None)

                    logger.error(
                        "Provider failure while analyzing file %s (HTTP %s): %s",
                        chunk_meta["filename"],
                        status_code,
                        error_msg,
                    )

                    llm_failures.append(f"{chunk_meta['filename']}: {error_msg}")

                    if status_code in (400, 401, 402, 403):
                        raise exc

        # Service down: every file failed and we have zero usable issues — do NOT report 100/100
        if valid_chunks and not all_issues and (chunk_errors or llm_failures):
            reason = _unavailable_reason(chunk_errors, llm_failures)
            _handle_review_unavailable(
                review=review,
                reason=reason,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                installation_id=installation_id,
                head_sha=head_sha,
            )
            return

        if llm_failures and not all_issues:
            raise RuntimeError("LLM analysis failed for all files. First error: " + llm_failures[0])

        # Step 3: Noise Filters and Capacity Limits
        if len(chunks) > 20:
            all_issues = [i for i in all_issues if i["severity"] in ("CRITICAL", "WARNING")]

        # Filter skip_categories from config
        if config.skip_categories:
            all_issues = [
                i for i in all_issues
                if i.get("category", "DSA").upper() not in config.skip_categories
            ]

        # Use config max_comments instead of hardcoded MAX_COMMENTS
        all_issues = all_issues[:config.max_comments]

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
                fixed_code_before=issue.get("fixed_code", {}).get("before", "") if isinstance(issue.get("fixed_code"), dict) else "",
                fixed_code_after=issue.get("fixed_code",  {}).get("after",  "") if isinstance(issue.get("fixed_code"), dict) else "",
            ) for issue in all_issues
        ]
        
        with transaction.atomic():
            Comment.objects.filter(review=review).delete()  # Safeguard against duplicated retries
            Comment.objects.bulk_create(comments_to_create)

        # Step 5: Compute metrics and close operations
        score = compute_health_score(all_issues)
        parse_warn = _parse_failure_note(chunk_errors)
        _mark_complete(review, score=score)

        logger.info(
            "Review %s complete — Issues logged: %s, Computed Score: %s",
            review_id,
            len(all_issues),
            score,
        )

        # Step 6: Post inline comments to GitHub via direct memory variables
        from github_client.comment_poster import post_github_review
        large_files = [
            c["filename"] for c in chunks
            if c.get("is_large") and not c["skipped"]
        ]
        post_github_review(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            installation_id=installation_id,
            head_sha=head_sha,
            comments=comments_to_create,
            health_score=score,
            large_files=large_files,
            service_note=parse_warn,
        )
        # ── Step 7: Post commit status ────────────────────────────────────────
        try:
            post_commit_status(
                repo_full_name=repo_full_name,
                head_sha=head_sha,
                installation_id=installation_id,
                health_score=score,
                issue_count=len(all_issues),
                fail_threshold=config.fail_threshold,
            )
        except Exception as status_err:
            logger.error(f"⚠️ Non-blocking warning: Failed to submit commit status metric: {status_err}")
            

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
        try:
            post_commit_status_unavailable(
                repo_full_name=repo_full_name,
                head_sha=head_sha,
                installation_id=installation_id,
            )
            post_unavailable_review_comment(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                installation_id=installation_id,
                detail=str(exc),
            )
        except Exception:
            pass
        raise exc


def compute_health_score(issues: list[dict]) -> int:
    deductions = {"CRITICAL": 20, "WARNING": 8, "SUGGESTION": 2}
    total = sum(deductions.get(str(i["severity"]).upper(), 0) for i in issues)
    return max(0, 100 - total)


def _unavailable_reason(chunk_errors: list, llm_failures: list) -> str:
    if chunk_errors:
        _fname, code = chunk_errors[0]
        if code == CHUNK_ERROR_PARSE_FAILED:
            return "Could not parse the AI review response (often caused by suggested-code formatting)."
        if code == CHUNK_ERROR_SERVICE_UNAVAILABLE:
            return "All LLM API keys are exhausted or unavailable."
        if code == CHUNK_ERROR_PROVIDER_EXHAUSTED:
            return "The AI provider failed after multiple retries."
    if llm_failures:
        return llm_failures[0]
    return "Review service unavailable."


def _parse_failure_note(chunk_errors: list) -> str | None:
    failed = [f for f, c in chunk_errors if c == CHUNK_ERROR_PARSE_FAILED]
    if not failed:
        return None
    names = ", ".join(f"`{n}`" for n in failed[:5])
    return (
        f"Some files could not be fully analyzed ({names}). "
        "Inline suggested fixes may be missing for those files."
    )


def _handle_review_unavailable(
    review,
    reason: str,
    repo_full_name: str,
    pr_number: int,
    installation_id: int,
    head_sha: str,
):
    """Mark review failed and tell GitHub we are down — never show a fake 100/100."""
    review.status = Review.Status.FAILED
    review.health_score = None
    review.error_message = reason
    review.completed_at = timezone.now()
    review.save(update_fields=["status", "health_score", "error_message", "completed_at"])

    logger.critical("Review %s unavailable: %s", review.id, reason)

    try:
        post_commit_status_unavailable(
            repo_full_name=repo_full_name,
            head_sha=head_sha,
            installation_id=installation_id,
        )
    except Exception as status_err:
        logger.error("Failed to post unavailable commit status: %s", status_err)

    try:
        post_unavailable_review_comment(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            installation_id=installation_id,
            detail=reason,
        )
    except Exception as comment_err:
        logger.error("Failed to post unavailable PR comment: %s", comment_err)


def _mark_complete(review, score: int):
    review.status = Review.Status.COMPLETED
    review.health_score = score
    review.completed_at = timezone.now()
    review.save(update_fields=["status", "health_score", "completed_at"])