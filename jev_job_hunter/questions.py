"""All Jev instructions, thresholds, and constants."""

MAX_LINKS_TO_SCORE = 100
MAX_SNAPSHOT_ELEMENTS = 250
MAX_JOB_FILTER = 40
MIN_JOB_LINKS = 3
MAX_JOBS_OPEN_PER_COMPANY = 3
NAV_CLICK_THRESHOLD = 0.55
MAX_NAVIGATION_STEPS_PER_COMPANY = 8
JOB_POSTING_THRESHOLD = 0.5
RELEVANCE_THRESHOLD = 0.7
FIT_SAVE_THRESHOLD = 0.7
MAX_BOARD_NAV_LINKS = 6
JOB_TEXT_LIMIT = 6000
MAX_RECENT_ACTIONS = 6
MAX_CONTROLS_TO_SCORE = 50
MAX_FILTER_CLICKS = 3
MAX_FILTER_ROUNDS = 2
MAX_SCROLLS = 8
FILTER_CLICK_THRESHOLD = 0.55
MORE_CLICK_THRESHOLD = 0.55

PAGE_KIND_CRITERIA = {
    "homepage": "Company marketing or home page, not a job listing.",
    "careers_hub": "Careers landing without specific job-title links.",
    "job_list": "A listing of specific open job postings.",
    "job_detail": "A single job description page.",
    "other": "Blog, docs, login, legal, or product.",
}
PAGE_KIND_INSTRUCTIONS = (
    "What page is this given `url` and `links`? "
    "homepage, careers_hub, job_list, job_detail, or other."
)


def leads_to_jobs_instructions(i: int, text: str, href: str) -> str:
    text = (text or "").replace('"', "'")[:80]
    href = (href or "").replace('"', "'")[:80]
    return (
        f'Does the link `links[{i}]` with text "{text}" and href "{href}" '
        "lead to jobs, careers, hiring, or open roles?"
    )


def is_job_posting_instructions(i: int, text: str, href: str) -> str:
    text = (text or "").replace('"', "'")[:80]
    href = (href or "").replace('"', "'")[:80]
    return (
        f'Is the link `links[{i}]` with text "{text}" and href "{href}" '
        "a specific job posting (a single role title), not navigation and not an apply button?"
    )


def is_software_related_instructions(j: int, title: str) -> str:
    title = (title or "").replace('"', "'")[:80]
    return f'Is the job `jobs[{j}]` titled "{title}" a software-engineering role?'


def is_ai_related_instructions(j: int, title: str) -> str:
    title = (title or "").replace('"', "'")[:80]
    return f'Is the job `jobs[{j}]` titled "{title}" an AI or ML role?'


def apply_filter_instructions(i: int, text: str) -> str:
    text = (text or "").replace('"', "'")[:60]
    return (
        f'Given `candidate_profile` and `hunt_query`, should we APPLY this jobs-board '
        f'filter/facet/chip `controls[{i}]` labeled "{text}" to narrow listings toward '
        f'the query (team, department, discipline, role family)? Yes if it matches the '
        f'query or target roles. No for the site header, footer, product nav, cookies, language, login, or G&A/sales '
    )


def browse_more_instructions(i: int, text: str) -> str:
    text = (text or "").replace('"', "'")[:60]
    return (
        f'Is `controls[{i}]` labeled "{text}" a control that reveals MORE job listings '
        f'or listing filters — next page, load more, show more, or a dropdown that opens '
        f'team/department/role facets or a title search box on the listings board? '
    )


DETAIL_NOULS = {
    "ai_relevance": "Given `candidate_profile` and `job`, is this role substantially about AI/ML?",
    "software_relevance": "Given `candidate_profile` and `job`, is this a software engineering role?",
    "agent_llm_relevance": "Given `candidate_profile` and `job`, does this role involve LLM agents or tool-use?",
    "backend_fullstack_relevance": "Given `candidate_profile` and `job`, is this primarily backend or full-stack?",
    "overall_fit": "Given `candidate_profile` and `job`, should this candidate save this posting?",
}
