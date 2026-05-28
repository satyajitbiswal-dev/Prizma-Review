from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_pr_review(self, review_id, repo_full_name, pr_number,
                      installation_id, head_sha):
    """
    Day 1: just logs the job was received.
    Day 2: we'll add PyGithub diff fetching here.
    """
    logger.info(
        f"[Task received] review={review_id} repo={repo_full_name} "
        f"pr=#{pr_number} sha={head_sha[:7]}"
    )

    # TODO Day 2: fetch diff, chunk by file
    # TODO Day 3: send to Gemini, parse JSON
    # TODO Day 4: post inline comments to GitHub

    