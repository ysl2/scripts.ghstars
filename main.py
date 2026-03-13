import os
import re
import asyncio
import xml.etree.ElementTree as ET
import aiohttp
from notion_client import AsyncClient
from dotenv import load_dotenv


load_dotenv()


# 并发控制配置
GITHUB_CONCURRENT_LIMIT = 5  # GitHub API 最大并发数
NOTION_CONCURRENT_LIMIT = 3  # Notion API 最大并发数
REQUEST_DELAY = 0.2  # 每个请求之间的最小间隔（秒）
HTTP_TOTAL_TIMEOUT = 20  # 外部 HTTP 请求总超时（秒）
HTTP_CONNECT_TIMEOUT = 10  # 外部 HTTP 建连超时（秒）
MAX_RETRIES = 2  # 对临时性错误的最大重试次数


# ANSI 颜色代码
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    GRAY = '\033[90m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def colored(text: str, color: str) -> str:
    """给文本添加颜色"""
    return f'{color}{text}{Colors.RESET}'


def clean_database_id(database_id):
    """清理 database ID，移除可能的 URL 参数"""
    if '?' in database_id:
        database_id = database_id.split('?')[0]
    return database_id


def is_valid_github_repo_url(url):
    """验证是否是合法的 GitHub 仓库 URL"""
    if not url or not isinstance(url, str):
        return False

    url = url.strip()
    pattern = r'^(https?://)?(www\.)?github\.com/[\w.-]+/[\w.-]+/?(.git)?$'

    return bool(re.match(pattern, url, re.IGNORECASE))


def extract_owner_repo(github_url):
    """从 GitHub URL 中提取 owner 和 repo"""
    if not is_valid_github_repo_url(github_url):
        return None

    url = github_url.strip()
    url = re.sub(r'^(https?://)?(www\.)?', '', url, flags=re.IGNORECASE)
    url = re.sub(r'^github\.com/', '', url, flags=re.IGNORECASE)
    url = re.sub(r'(\.git)?/?$', '', url)

    parts = url.split('/')
    if len(parts) >= 2:
        return parts[0], parts[1]

    return None


def get_github_url_from_page(page):
    """从 page 中提取 Github 字段的值"""
    github_property = page.get('properties', {}).get('Github', {})

    if github_property.get('type') == 'url':
        return github_property.get('url')
    elif github_property.get('type') == 'rich_text':
        rich_text = github_property.get('rich_text', [])
        if rich_text:
            return rich_text[0].get('text', {}).get('content', '')
    return None


def get_current_stars_from_page(page):
    """从 page 中获取当前的 Github stars 字段值"""
    stars_property = page.get('properties', {}).get('Github stars', {})

    if stars_property.get('type') == 'number':
        return stars_property.get('number')
    return None


def classify_github_value(value):
    """将 Github 字段分类为 valid_github / empty / wip / other"""
    if value is None:
        return 'empty'

    if not isinstance(value, str):
        value = str(value)

    normalized = value.strip()
    if not normalized:
        return 'empty'
    if normalized.lower() == 'wip':
        return 'wip'
    if is_valid_github_repo_url(normalized):
        return 'valid_github'
    return 'other'


def normalize_github_url(url: str):
    """标准化 GitHub 仓库 URL，统一为 https://github.com/owner/repo"""
    result = extract_owner_repo(url)
    if not result:
        return None
    owner, repo = result
    return f'https://github.com/{owner}/{repo}'


def find_github_url_in_text(text: str):
    """从任意文本中提取第一个合法的 GitHub 仓库 URL"""
    if not text or not isinstance(text, str):
        return None

    pattern = r'https?://(?:www\.)?github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?[),.;:!?]*'
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    for match in matches:
        cleaned = match.rstrip('),.;:!?')
        normalized = normalize_github_url(cleaned)
        if normalized:
            return normalized
    return None


def get_text_from_property(prop: dict):
    """从 Notion property 中提取文本值（若可表示为文本）"""
    if not isinstance(prop, dict):
        return None

    prop_type = prop.get('type')
    if prop_type in {'rich_text', 'title'}:
        items = prop.get(prop_type, [])
        parts = [item.get('plain_text', '') for item in items if item.get('plain_text')]
        return ''.join(parts) or None
    if prop_type == 'url':
        return prop.get('url') or None
    if prop_type == 'formula':
        formula = prop.get('formula', {})
        if formula.get('type') == 'string':
            return formula.get('string') or None
    return None


def find_github_url_in_json_payload(payload):
    """递归扫描 JSON 样式 payload，寻找第一个合法 GitHub 仓库链接"""
    if isinstance(payload, str):
        return find_github_url_in_text(payload)
    if isinstance(payload, list):
        for item in payload:
            result = find_github_url_in_json_payload(item)
            if result:
                return result
        return None
    if isinstance(payload, dict):
        for value in payload.values():
            result = find_github_url_in_json_payload(value)
            if result:
                return result
        return None
    return None


def find_github_url_in_alphaxiv_legacy_payload(payload):
    """按 legacy AlphaXiv paper JSON 的常见字段提取 GitHub URL"""
    if not isinstance(payload, dict):
        return None

    paper = payload.get('paper', {}) if isinstance(payload.get('paper'), dict) else {}
    candidates = [
        paper.get('implementation'),
        paper.get('marimo_implementation'),
        paper.get('paper_group', {}).get('resources') if isinstance(paper.get('paper_group'), dict) else None,
        paper.get('resources'),
    ]

    for candidate in candidates:
        github_url = find_github_url_in_json_payload(candidate)
        if github_url:
            return github_url

    return find_github_url_in_json_payload(payload)


ABSTRACT_PROPERTY_CANDIDATES = ('Abstract', 'Summary', 'TL;DR', 'Notes')
ARXIV_PROPERTY_CANDIDATES = ('URL', 'Arxiv', 'arXiv', 'Paper URL', 'Link')


def get_abstract_text_from_page(page: dict):
    """从页面候选属性中提取摘要文本"""
    properties = page.get('properties', {})
    for name in ABSTRACT_PROPERTY_CANDIDATES:
        value = get_text_from_property(properties.get(name, {}))
        if value and value.strip():
            return value.strip()
    return None


def extract_arxiv_id_from_url(url: str):
    """从 arXiv URL 中提取 arXiv ID"""
    if not url or not isinstance(url, str):
        return None

    match = re.search(r'arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?(?:\.pdf)?', url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def get_arxiv_id_from_page(page: dict):
    """从页面候选属性中提取 arXiv ID"""
    properties = page.get('properties', {})
    for name in ARXIV_PROPERTY_CANDIDATES:
        value = get_text_from_property(properties.get(name, {}))
        arxiv_id = extract_arxiv_id_from_url(value) if value else None
        if arxiv_id:
            return arxiv_id
    return None


def get_page_title(page):
    """获取页面标题"""
    properties = page.get('properties', {})
    for key in ('Name', 'Title'):
        title_prop = properties.get(key, {})
        if title_prop.get('type') == 'title':
            title_list = title_prop.get('title', [])
            if title_list:
                return title_list[0].get('plain_text', '')
    return ''


def normalize_title_for_matching(title: str):
    """标准化标题用于匹配打分"""
    if not title or not isinstance(title, str):
        return ''
    return ' '.join(title.split()).strip().lower()


def extract_best_arxiv_id_from_feed(feed_xml: str, title_query: str):
    """从 arXiv Atom feed 中按标题匹配提取最佳 arXiv ID，并返回匹配来源"""
    if not feed_xml or not title_query:
        return None, None

    ns = {'a': 'http://www.w3.org/2005/Atom'}
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return None, None

    title_query_norm = normalize_title_for_matching(title_query)
    best_id = None
    best_score = -1
    best_source = None

    for entry in root.findall('a:entry', ns):
        title_el = entry.find('a:title', ns)
        id_el = entry.find('a:id', ns)
        if title_el is None or id_el is None or not title_el.text or not id_el.text:
            continue

        title = normalize_title_for_matching(title_el.text)
        entry_id = id_el.text.strip()
        match = re.search(r'/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?$', entry_id)
        if not match:
            continue

        arxiv_id = match.group(1)
        score = 0
        source = None
        if title == title_query_norm:
            score = 100
            source = 'title_search_exact'
        elif title_query_norm in title:
            score = 80
            source = 'title_search_contained'
        elif title in title_query_norm:
            score = 60
            source = 'title_search_contains_entry'

        if score > best_score:
            best_score = score
            best_id = arxiv_id
            best_source = source

    return best_id, best_source


def get_page_url(page):
    """获取页面的 Notion URL"""
    return page.get('url', '')


def load_config_from_env(env: dict[str, str]) -> dict[str, str]:
    """从环境变量读取配置并校验必填项"""
    notion_token = (env.get('NOTION_TOKEN') or '').strip()
    github_token = (env.get('GITHUB_TOKEN') or '').strip()
    alphaxiv_api_key = (env.get('ALPHAXIV_API_KEY') or '').strip()
    database_id = (env.get('DATABASE_ID') or '').strip()

    missing = []
    if not notion_token:
        missing.append('NOTION_TOKEN')
    if not database_id:
        missing.append('DATABASE_ID')

    if missing:
        joined = ', '.join(missing)
        raise ValueError(f'Missing required environment variables: {joined}')

    return {
        'notion_token': notion_token,
        'github_token': github_token,
        'alphaxiv_api_key': alphaxiv_api_key,
        'database_id': database_id,
    }


def get_github_headers(github_token: str):
    """获取 GitHub API 请求头"""
    headers = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'notion-github-stars-updater'}
    if github_token:
        headers['Authorization'] = f'Bearer {github_token}'
    return headers


def get_alphaxiv_headers(alphaxiv_api_key: str):
    """获取 AlphaXiv API 请求头"""
    headers = {'Accept': 'application/json', 'User-Agent': 'notion-github-stars-updater'}
    if alphaxiv_api_key:
        headers['Authorization'] = f'Bearer {alphaxiv_api_key}'
    return headers


# 不重要的跳过原因（显示为灰色）
MINOR_SKIP_REASONS = {
    'Invalid Github URL format',
    'No Github URL found',
    'Cannot extract owner/repo',
    'Unsupported Github field content',
    'Missing ALPHAXIV_API_KEY',
    'No arXiv ID found for AlphaXiv API lookup',
    'No Github URL found in AlphaXiv API',
    'Discovered URL is not a valid GitHub repository',
}
MINOR_SKIP_REASON_PREFIXES = (
    'AlphaXiv API error',
    'AlphaXiv API timeout',
    'AlphaXiv API request failed:',
    'arXiv API error',
    'arXiv API timeout',
    'arXiv API request failed:',
)


def is_minor_skip_reason(reason: str) -> bool:
    """判断是否是不重要的跳过原因"""
    return reason in MINOR_SKIP_REASONS or any(reason.startswith(prefix) for prefix in MINOR_SKIP_REASON_PREFIXES)


class RateLimiter:
    """速率限制器，控制请求频率"""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last_request_time = 0
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = asyncio.get_event_loop().time()
            time_since_last = now - self.last_request_time
            if time_since_last < self.min_interval:
                await asyncio.sleep(self.min_interval - time_since_last)
            self.last_request_time = asyncio.get_event_loop().time()


class GitHubClient:
    """外部 HTTP 异步客户端，复用 GitHub 速率限制设置"""

    def __init__(self, max_concurrent: int, min_interval: float, github_token: str, alphaxiv_api_key: str = ''):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rate_limiter = RateLimiter(min_interval)
        self.github_token = github_token
        self.alphaxiv_api_key = alphaxiv_api_key
        self.session = None
        self.rate_limit_remaining = None
        self.rate_limit_reset = None

    async def request_with_retry(self, method: str, url: str, *, headers=None, params=None, expect='json', retry_prefix='Request'):
        """带超时与有限重试的通用 HTTP 请求"""
        retriable_statuses = {429, 500, 502, 503, 504}

        for attempt in range(MAX_RETRIES + 1):
            async with self.semaphore:
                await self.rate_limiter.acquire()
                try:
                    async with self.session.request(method, url, headers=headers, params=params) as response:
                        if response.status == 200:
                            if expect == 'json':
                                return await response.json(), None
                            return await response.text(), None

                        if response.status in retriable_statuses and attempt < MAX_RETRIES:
                            await asyncio.sleep(0.5 * (2**attempt))
                            continue

                        return None, f'{retry_prefix} error ({response.status})'
                except asyncio.TimeoutError:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, f'{retry_prefix} timeout'
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return None, f'{retry_prefix} request failed: {e}'

    async def get_arxiv_feed_by_title(self, title: str):
        """通过标题查询 arXiv Atom feed"""
        url = 'https://export.arxiv.org/api/query'
        params = {
            'search_query': f'ti:"{title}"',
            'start': '0',
            'max_results': '10',
        }
        return await self.request_with_retry(
            'GET',
            url,
            params=params,
            expect='text',
            retry_prefix='arXiv API',
        )

    async def get_alphaxiv_paper_legacy(self, arxiv_id: str):
        """通过 AlphaXiv legacy API 获取论文 JSON 数据"""
        if not self.alphaxiv_api_key:
            return None, 'Missing ALPHAXIV_API_KEY'

        url = f'https://api.alphaxiv.org/papers/v3/legacy/{arxiv_id}'
        headers = get_alphaxiv_headers(self.alphaxiv_api_key)
        return await self.request_with_retry(
            'GET',
            url,
            headers=headers,
            expect='json',
            retry_prefix='AlphaXiv API',
        )

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=HTTP_TOTAL_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT)
        self.session = aiohttp.ClientSession(headers=get_github_headers(self.github_token), timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def check_rate_limit(self):
        """检查 GitHub API 请求限额"""
        try:
            async with self.session.get('https://api.github.com/rate_limit') as response:
                if response.status == 200:
                    data = await response.json()
                    core = data.get('resources', {}).get('core', {})
                    self.rate_limit_remaining = core.get('remaining', 0)
                    self.rate_limit_reset = core.get('reset', 0)
                    return {
                        'remaining': self.rate_limit_remaining,
                        'limit': core.get('limit', 0),
                        'reset_time': self.rate_limit_reset,
                    }
        except Exception:
            pass
        return None

    async def wait_for_rate_limit_reset(self):
        """等待 rate limit 重置"""
        if self.rate_limit_reset:
            import time

            wait_seconds = self.rate_limit_reset - int(time.time()) + 1
            if wait_seconds > 0:
                print(colored(f'  ⏳ Rate limit exceeded. Waiting {wait_seconds} seconds...', Colors.YELLOW))
                await asyncio.sleep(wait_seconds)

    async def get_star_count(self, owner: str, repo: str):
        """
        获取 GitHub 仓库的 star 数量

        返回: (star_count, error_message)
        """
        async with self.semaphore:
            await self.rate_limiter.acquire()

            url = f'https://api.github.com/repos/{owner}/{repo}'

            try:
                async with self.session.get(url) as response:
                    self.rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
                    self.rate_limit_reset = int(response.headers.get('X-RateLimit-Reset', 0))

                    if response.status == 200:
                        data = await response.json()
                        return data.get('stargazers_count'), None
                    elif response.status == 404:
                        return None, 'Repository not found'
                    elif response.status == 403:
                        if self.rate_limit_remaining == 0:
                            await self.wait_for_rate_limit_reset()
                            async with self.session.get(url) as retry_response:
                                if retry_response.status == 200:
                                    data = await retry_response.json()
                                    return data.get('stargazers_count'), None
                        return None, 'Rate limit exceeded or access denied'
                    else:
                        return None, f'GitHub API error ({response.status})'

            except asyncio.TimeoutError:
                return None, 'Request timeout'
            except Exception as e:
                return None, f'Request failed: {e}'


class NotionClient:
    """Notion API 异步客户端包装器，带有并发限制"""

    def __init__(self, token: str, max_concurrent: int):
        self.client = AsyncClient(auth=token)
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def update_page_properties(self, page_id: str, *, github_url: str | None = None, stars_count: int | None = None):
        """通用页面属性更新，按需更新 Github / Github stars"""
        properties = {}
        if github_url is not None:
            properties['Github'] = {'url': github_url}
        if stars_count is not None:
            properties['Github stars'] = {'number': stars_count}
        if not properties:
            return

        async with self.semaphore:
            await self.client.pages.update(page_id=page_id, properties=properties)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def get_data_source_id(self, database_id: str):
        """获取 database 对应的 data_source_id"""
        async with self.semaphore:
            database = await self.client.databases.retrieve(database_id=clean_database_id(database_id))
            data_sources = database.get('data_sources', [])
            if data_sources:
                return data_sources[0].get('id')
            return None

    async def query_pages(self, data_source_id: str):
        """查询所有页面，后续在本地决定是否处理"""
        pages = []

        async with self.semaphore:
            results = await self.client.data_sources.query(data_source_id=data_source_id)

        pages.extend(results.get('results', []))

        while results.get('has_more'):
            async with self.semaphore:
                results = await self.client.data_sources.query(
                    data_source_id=data_source_id,
                    start_cursor=results.get('next_cursor'),
                )
            pages.extend(results.get('results', []))

        return pages



async def resolve_arxiv_id_for_page(page: dict, github_client: GitHubClient):
    """统一解析页面对应的 arXiv ID：优先 URL 字段，其次标题检索"""
    arxiv_id = get_arxiv_id_from_page(page)
    if arxiv_id:
        return arxiv_id, 'url_field', None

    title = get_page_title(page)
    if not title:
        return None, None, 'No arXiv ID found for AlphaXiv API lookup'

    feed_xml, error = await github_client.get_arxiv_feed_by_title(title)
    if error:
        return None, None, error

    arxiv_id, source = extract_best_arxiv_id_from_feed(feed_xml, title)
    if arxiv_id:
        return arxiv_id, source, None
    return None, None, 'No arXiv ID found for AlphaXiv API lookup'


def format_resolution_source_label(source: str | None, arxiv_source: str | None = None):
    """格式化日志中的来源标签"""
    if source == 'existing':
        return 'existing Github'
    if source == 'alphaxiv_api':
        mapping = {
            'url_field': 'AlphaXiv API fallback (from URL field)',
            'title_search_exact': 'AlphaXiv API fallback (from title search: exact match)',
            'title_search_contained': 'AlphaXiv API fallback (from title search: contained match)',
            'title_search_contains_entry': 'AlphaXiv API fallback (from title search: reverse contained match)',
        }
        return mapping.get(arxiv_source, 'AlphaXiv API fallback')
    return 'unknown source'


async def discover_github_url_from_alphaxiv_api(page: dict, github_client: GitHubClient):
    """从 AlphaXiv legacy API 中发现 GitHub 仓库链接"""
    arxiv_id, arxiv_source, error = await resolve_arxiv_id_for_page(page, github_client)
    if not arxiv_id:
        return None, arxiv_source, error

    payload, error = await github_client.get_alphaxiv_paper_legacy(arxiv_id)
    if error:
        return None, arxiv_source, error

    github_url = find_github_url_in_alphaxiv_legacy_payload(payload)
    if github_url:
        return github_url, arxiv_source, None
    return None, arxiv_source, 'No Github URL found in AlphaXiv API'


async def resolve_repo_for_page(page: dict, github_client: GitHubClient):
    """统一解析页面最终应使用的 GitHub 仓库 URL"""
    github_value = get_github_url_from_page(page)
    github_state = classify_github_value(github_value)

    if github_state == 'valid_github':
        return {
            'github_url': normalize_github_url(github_value),
            'source': 'existing',
            'needs_github_update': False,
            'reason': None,
        }

    if github_state == 'other':
        return {
            'github_url': None,
            'source': None,
            'needs_github_update': False,
            'reason': 'Unsupported Github field content',
        }

    github_url, arxiv_source, error = await discover_github_url_from_alphaxiv_api(page, github_client)
    if github_url:
        return {
            'github_url': github_url,
            'source': 'alphaxiv_api',
            'arxiv_source': arxiv_source,
            'needs_github_update': True,
            'reason': None,
        }

    return {
        'github_url': None,
        'source': None,
        'arxiv_source': arxiv_source,
        'needs_github_update': False,
        'reason': error or 'No Github URL found',
    }


async def process_page(
    page: dict,
    index: int,
    total: int,
    github_client: GitHubClient,
    notion_client: NotionClient,
    results: dict,
    lock: asyncio.Lock,
):
    """处理单个页面"""
    page_id = page['id']
    current_stars = get_current_stars_from_page(page)
    title = get_page_title(page) or page_id
    notion_url = get_page_url(page)

    resolution = await resolve_repo_for_page(page, github_client)
    github_url = resolution['github_url']
    if not github_url:
        reason = resolution['reason']
        async with lock:
            print(colored(f'[{index}/{total}] {title}', Colors.GRAY))
            print(colored(f'  ⏭️ Skipped: {reason}', Colors.GRAY))
            results['skipped'].append({'title': title, 'github_url': None, 'notion_url': notion_url, 'reason': reason})
        return

    result = extract_owner_repo(github_url)
    if not result:
        reason = 'Discovered URL is not a valid GitHub repository'
        async with lock:
            print(colored(f'[{index}/{total}] {title}', Colors.GRAY))
            print(colored(f'  ⏭️ Skipped: {reason}', Colors.GRAY))
            results['skipped'].append(
                {'title': title, 'github_url': github_url, 'notion_url': notion_url, 'reason': reason}
            )
        return

    owner, repo = result

    new_stars, error = await github_client.get_star_count(owner, repo)
    if error:
        async with lock:
            print(colored(f'[{index}/{total}] {title}', Colors.RED))
            print(colored(f'  📍 {owner}/{repo}', Colors.RED))
            print(colored(f'  ⏭️ Skipped: {error}', Colors.RED))
            results['skipped'].append(
                {'title': title, 'github_url': github_url, 'notion_url': notion_url, 'reason': error}
            )
        return

    await notion_client.update_page_properties(
        page_id,
        github_url=github_url if resolution['needs_github_update'] else None,
        stars_count=new_stars,
    )

    async with lock:
        print(f'[{index}/{total}] {title}')
        current_stars_display = current_stars if current_stars is not None else 'N/A'
        source_label = format_resolution_source_label(resolution['source'], resolution.get('arxiv_source'))
        print(f'  📍 {owner}/{repo} | Current stars: {current_stars_display}')
        print(f'  🔎 Source: {source_label}')

        if resolution['needs_github_update']:
            print(f'  🔗 Github set to: {github_url}')

        if current_stars is not None:
            diff = new_stars - current_stars
            if diff > 0:
                diff_display = colored(f'+{diff}', Colors.GREEN)
            elif diff < 0:
                diff_display = colored(str(diff), Colors.RED)
            else:
                diff_display = '±0'
            print(f'  ✅ Updated: {current_stars} → {new_stars} ({diff_display})')
        else:
            print(f'  ✅ Updated: N/A → {new_stars}')

        results['updated'] += 1


async def main():
    config = load_config_from_env(dict(os.environ))
    github_token = config['github_token']
    notion_token = config['notion_token']
    database_id = config['database_id']

    # 检查 GitHub Token 状态
    if github_token:
        print(colored('✅ GitHub Token configured (5000 requests/hour)', Colors.GREEN))
    else:
        print(colored('⚠️ No GitHub Token configured (60 requests/hour)', Colors.YELLOW))
        print('   Set GITHUB_TOKEN environment variable for higher rate limit')

    print(f'⚙️ Concurrency: GitHub={GITHUB_CONCURRENT_LIMIT}, Notion={NOTION_CONCURRENT_LIMIT}')
    print(f'⚙️ Request interval: {REQUEST_DELAY}s')
    print()

    async with GitHubClient(GITHUB_CONCURRENT_LIMIT, REQUEST_DELAY, github_token, config['alphaxiv_api_key']) as github_client:
        async with NotionClient(notion_token, NOTION_CONCURRENT_LIMIT) as notion_client:
            # 检查 rate limit
            rate_info = await github_client.check_rate_limit()
            if rate_info:
                print(f'📊 GitHub API Rate Limit: {rate_info["remaining"]}/{rate_info["limit"]} remaining')
            print()

            # 获取 data source ID
            data_source_id = await notion_client.get_data_source_id(database_id)
            if not data_source_id:
                print(colored('❌ 无法获取 data_source_id，请检查 database_id 是否正确', Colors.RED))
                return

            print(f'📚 Data source ID: {data_source_id}')

            # 查询所有页面
            pages = await notion_client.query_pages(data_source_id)
            print(f'📝 Found {len(pages)} pages with Github field\n')

            # 处理结果
            results = {'updated': 0, 'skipped': []}
            lock = asyncio.Lock()

            # 创建所有任务
            tasks = [
                process_page(page, i, len(pages), github_client, notion_client, results, lock)
                for i, page in enumerate(pages, 1)
            ]

            # 并发执行
            await asyncio.gather(*tasks)

            # 最终汇总
            print(f'\n{"=" * 60}')
            print(colored(f'✅ Updated: {results["updated"]}', Colors.GREEN))
            print(f'⏭️ Skipped: {len(results["skipped"])}')

            # 分类跳过的项目
            minor_skipped = [s for s in results['skipped'] if is_minor_skip_reason(s['reason'])]
            major_skipped = [s for s in results['skipped'] if not is_minor_skip_reason(s['reason'])]

            # 显示重要的跳过项目（红色）
            if major_skipped:
                print(f'\n{"=" * 60}')
                print(colored('❌ Failed rows (need attention):', Colors.RED))
                print(f'{"=" * 60}')
                for i, item in enumerate(major_skipped, 1):
                    print(colored(f'\n{i}. {item["title"]}', Colors.RED))
                    print(colored(f'   Reason:     {item["reason"]}', Colors.RED))
                    if item['github_url']:
                        print(colored(f'   Github URL: {item["github_url"]}', Colors.RED))
                    print(colored(f'   Notion URL: {item["notion_url"]}', Colors.RED))

            # 显示不重要的跳过项目（灰色）
            if minor_skipped:
                print(f'\n{"=" * 60}')
                print(colored('⏭️ Skipped rows (non-GitHub URLs, can be ignored):', Colors.GRAY))
                print(colored(f'{"=" * 60}', Colors.GRAY))
                for i, item in enumerate(minor_skipped, 1):
                    print(colored(f'\n{i}. {item["title"]}', Colors.GRAY))
                    print(colored(f'   Reason:     {item["reason"]}', Colors.GRAY))
                    if item['github_url']:
                        print(colored(f'   Github URL: {item["github_url"]}', Colors.GRAY))
                    print(colored(f'   Notion URL: {item["notion_url"]}', Colors.GRAY))

            # 显示最终 rate limit 状态
            print(f'\n{"=" * 60}')
            rate_info = await github_client.check_rate_limit()
            if rate_info:
                print(f'📊 GitHub API Rate Limit: {rate_info["remaining"]}/{rate_info["limit"]} remaining')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except ValueError as exc:
        print(colored(f'❌ {exc}', Colors.RED))
