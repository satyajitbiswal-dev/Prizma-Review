from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.conf import settings
from reviews.models import Repo, PullRequest, Review, Comment
from collections import defaultdict
from django.core.paginator import Paginator
from github_client.installations import sync_installations_for_user, repos_queryset_for_user


@login_required
def dashboard_home(request):
    """Repositories where the GitHub App is installed for this user."""
    sync_installations_for_user(request.user)

    repos = repos_queryset_for_user(request.user).annotate(
        total_prs=Count('pull_requests', distinct=True),
        avg_score=Avg(
            'pull_requests__reviews__health_score',
            filter=Q(pull_requests__reviews__status='completed'),
        ),
        critical_issues=Count(
            'pull_requests__reviews__comments',
            filter=Q(
                pull_requests__reviews__comments__severity='critical',
                pull_requests__reviews__status='completed',
            ),
        ),
    ).order_by('-created_at')

    inactive_count = Repo.objects.filter(
        Q(owner_login__iexact=request.user.username)
        | Q(full_name__istartswith=f"{request.user.username}/"),
        is_active=False,
    ).count()

    context = {
        'repos': repos,
        'github_app_install_url': settings.GITHUB_APP_INSTALL_URL,
        'install_hint': _install_hint(repos.exists(), inactive_count),
    }
    return render(request, 'dashboard/home.html', context)


def _install_hint(has_repos: bool, inactive_count: int) -> str:
    if has_repos:
        return ""
    if inactive_count:
        return "removed"
    return "not_synced"


@login_required
def repo_detail(request, repo_id):
    """Displays the PR historical ledger and a line trend visualization dataset."""
    repo = get_object_or_404(Repo, id=repo_id)
    
    # Get pull requests sorted by newest, along with their latest reviews
    pull_requests = PullRequest.objects.filter(repo=repo).prefetch_related('reviews').order_by('-created_at')
    
    #  Paginator (forces exactly 10 rows per fetch block)
    paginator = Paginator(pull_requests, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    # Chart optimization: Pull the last 10 items and arrange them oldest-to-newest
    chart_prs = list(pull_requests[:10])
    chart_prs.reverse()

     # Pre-calculate data coordinates for the line charts (last 10 completed review scores)\
    historical_scores = list(
        Review.objects.filter(pull_request__repo=repo, status='completed')
        .order_by('-created_at')
        .values_list('health_score', flat=True)[:10]
    )

    historical_scores.reverse()
    # Compute overall aggregate fleet average score
    avg_score = Review.objects.filter(pull_request__repo=repo, status='completed').aggregate(Avg('health_score'))['health_score__avg'] or 0

    context = {
        'repo': repo,
        'pull_requests': page_obj,
        'historical_scores': historical_scores,
        'avg_score': round(avg_score, 1),
    }

    if request.headers.get('HX-Request') and request.GET.get('page'):
        return render(request, 'dashboard/partials/pr_rows.html', context)
    return render(request, 'dashboard/repo_detail.html', context)


def pr_review_status_element(request, pr_id):
    """HTMX polling endpoint returning an isolated template component block."""
    pr = get_object_or_404(PullRequest, id=pr_id)
    latest_review = pr.reviews.order_by('-created_at').first()
    
    context = {
        'pr': pr,
        'latest_review': latest_review
    }
    return render(request, 'dashboard/partials/pr_row_status.html', context)


@login_required
def review_detail(request, review_id):
    """Displays the deep-dive analysis screen for a single PR code review."""
    review = get_object_or_404(Review.objects.select_related('pull_request__repo'), id=review_id)
    comments = review.comments.all().order_by('file_path', 'line_start')

    # Calculate exact counts for our severity metric cards
    severity_counts = {
        'critical': comments.filter(severity='critical').count(),
        'warning': comments.filter(severity='warning').count(),
        'suggestion': comments.filter(severity='suggestion').count(),
    }

    # Group comments by file path so they look structured on-screen
    grouped_comments = defaultdict(list)
    for comment in comments:
        grouped_comments[comment.file_path].append(comment)

    context = {
        'review': review,
        'pr': review.pull_request,
        'repo': review.pull_request.repo,
        'severity_counts': severity_counts,
        'grouped_comments': dict(grouped_comments),
    }
    return render(request, 'dashboard/partials/review_detail.html', context)