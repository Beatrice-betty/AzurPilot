# -*- coding: utf-8 -*-
"""WebUI 自定义背景图来源解析。

背景图默认由主题 CSS 写死（``--alas-apple-bg-image``），但那有两个问题：
一是启动器同步上游会覆盖 ``assets/`` 下的文件，用户改了也留不住；
二是只能挂一个固定地址，没法用本地图片。

这里把「用哪张图」挪到运行时决定，CSS 文件不参与：

1. ``bg/`` 目录有图片 → 随机取一张（优先）
2. 没有本地图但有自定义网址 → 随机取一个（接口地址每次请求都是新图）
3. 都没有 → 用主题 CSS 里写死的那张

配置与图片都放在被 gitignore 覆盖的路径里，所以启动器怎么覆盖上游都不影响。
"""
from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from module.logger import logger
from module.webui.lang import t

from module.webui.webui_prefs import (
    BACKGROUND_DIR_NAME,
    BACKGROUND_EXTENSIONS,
    get_background_urls,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

SOURCE_LOCAL = 'local'
SOURCE_REMOTE = 'remote'
SOURCE_DEFAULT = 'default'
SOURCE_MIXED = 'mixed'

PLACE_ROOT = 'root'
PLACE_EXTRACTED = 'extracted'

# 【提取】下载的图存这里，与用户自己放的图分开，避免混淆
EXTRACTED_DIR_NAME = '提取'

BACKGROUND_ROUTE = '/api/background'
EXTRACTED_ROUTE = f'{BACKGROUND_ROUTE}/extracted'

DEFAULT_URL_PATTERN = re.compile(
    r'--alas-apple-bg-image:\s*url\(\s*["\']?([^"\')]+)["\']?\s*\)'
)

def _readme_text() -> str:
    """bg/README.txt 的正文，按当前界面语言生成。

    推迟到写入时再取译文：模块导入期还没有语言上下文。
    """
    return t('Gui.Stat.Readme')


def background_dir() -> Path:
    """返回本地背景图目录 ``bg/``（不保证存在）。"""
    return _PROJECT_ROOT / BACKGROUND_DIR_NAME


def extracted_dir() -> Path:
    """返回【提取】下载目录 ``bg/提取/``（不保证存在）。"""
    return background_dir() / EXTRACTED_DIR_NAME


def ensure_background_dir() -> Path:
    """确保 ``bg/`` 与其子目录存在，返回 ``bg/`` 路径。

    目录不存在时创建，便于用户在界面上点【本地】直接打开。
    创建失败只告警——背景图是锦上添花，不该影响主流程。
    """
    d = background_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        extracted_dir().mkdir(parents=True, exist_ok=True)
        readme = d / 'README.txt'
        if not readme.exists():
            readme.write_text(
                _readme_text().format(
                    exts='、'.join(BACKGROUND_EXTENSIONS),
                    sub=EXTRACTED_DIR_NAME,
                ),
                encoding='utf-8',
            )
    except OSError as e:
        logger.warning(f'[WebUI-背景] 创建 {d} 失败: {e}')
    return d


def _images_in(directory: Path) -> list:
    """列出某目录下的图片文件名（排序，便于稳定测试）。"""
    try:
        if not directory.is_dir():
            return []
        names = [
            f.name
            for f in directory.iterdir()
            if f.is_file() and f.suffix.lower() in BACKGROUND_EXTENSIONS
        ]
    except OSError as e:
        logger.warning(f'[WebUI-背景] 读取 {directory} 失败: {e}')
        return []
    return sorted(names)


def list_images() -> list:
    """列出可用作背景的本地图（只有用户自己放的那些）。

    ``bg/提取/`` 是【提取】的存放处，**不参与随机**：那里的图是用户
    下载下来备用的，不该被自动套用成背景。想用它就手动挪到 ``bg/``。

    Returns:
        ``[{'name': str, 'place': 'root'}, ...]``。
    """
    return [{'name': n, 'place': PLACE_ROOT}
            for n in _images_in(background_dir())]


def extracted_count() -> int:
    """``bg/提取/`` 里已下载的图片数量（仅用于界面展示）。"""
    return len(_images_in(extracted_dir()))


def list_local_images() -> list:
    """列出所有可用背景图的文件名（兼容旧调用，只返回名字）。"""
    return [i['name'] for i in list_images()]


def local_image_url(name: str, place: str = PLACE_ROOT) -> str:
    """把本地图片转成可访问的地址。"""
    base = EXTRACTED_ROUTE if place == PLACE_EXTRACTED else BACKGROUND_ROUTE
    return f'{base}/{quote(name)}'


def default_background_url(theme_source: Optional[str] = None) -> str:
    """读主题 CSS 里写死的那张背景图地址（用户没配自定义时用的就是它）。

    Args:
        theme_source: 主题 CSS 文本；``None`` 时按当前主题自动取。

    Returns:
        地址；取不到时返回空串。
    """
    if theme_source is None:
        from module.webui.app_shell import theme_css_source
        theme_source = theme_css_source()
    match = DEFAULT_URL_PATTERN.search(theme_source or '')
    return match.group(1).strip() if match else ''


def resolve_direct_link(url: str, timeout: float = 10.0) -> dict:
    """跟随跳转，解析出「当前这张图」的直链。

    随机图接口（如 ``api.php``）每次请求都 302 到不同的图，
    所以直接给出接口地址没用——用户想要的是眼前这张的地址。

    Args:
        url: 待解析的地址。
        timeout: 单次请求超时（秒）。

    Returns:
        ``{'ok': bool, 'url': str, 'content_type': str, 'error': str}``。
    """
    try:
        import requests
    except ImportError as e:  # pragma: no cover - 依赖缺失时降级
        return {'ok': False, 'url': url, 'content_type': '',
                'error': f'requests 不可用: {e}'}

    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400:
            resp = requests.get(url, allow_redirects=True, timeout=timeout,
                                stream=True)
        final = resp.url or url
        content_type = resp.headers.get('Content-Type', '')
        resp.close()
        return {'ok': True, 'url': final, 'content_type': content_type,
                'error': ''}
    except Exception as e:  # noqa: BLE001 - 网络问题种类多，统一降级报告
        return {'ok': False, 'url': url, 'content_type': '', 'error': str(e)}


def _lock_remote(choice: dict) -> dict:
    """把网页来源锁定到具体一张图（就地向 choice 写入确定地址）。

    随机图接口每次请求都返回不同的图。只有在这里解析一次、之后一律用这个
    确定地址，显示的图和展示的直链才会是同一张 —— 否则浏览器请求一次、
    解析直链再请求一次，两次各自随机，永远对不上。

    解析失败（离线等）就保留原地址，背景照样能用，只是仍会随机。

    Args:
        choice: :func:`pick_background` 的结果。

    Returns:
        同一个 dict（已写入 ``css_url``）。
    """
    if choice.get('source') == SOURCE_LOCAL:
        return choice
    url = choice.get('value')
    if url:
        result = resolve_direct_link(url)
        if result.get('ok') and result.get('url'):
            choice['css_url'] = result['url']
    return choice


def pick_background(rng: Optional[random.Random] = None) -> dict:
    """随机选一张背景图。

    网页来源的抽签结果只是原始地址，**必须再交给** :func:`_lock_remote`
    锁定成具体图片，否则显示的图与直链会对不上。

    三类来源，优先级：

    1. ``bg/`` 有图 + 有自定义网址 → **混合**：两边一起随机
    2. 只有本地图 → 本地随机
    3. 只有自定义网址 → 网址随机
    4. 都没有 → 网页默认那张

    ``bg/提取/`` 不参与（那是存放处，见 :func:`list_images`）。

    Args:
        rng: 可注入的随机源，便于测试确定性。

    Returns:
        ``{'source': ..., 'value': ..., 'place': ..., 'css_url': ...}``。

        - ``source``：``local`` / ``remote`` / ``mixed`` / ``default``
        - ``value``：本地是文件名、remote 是自定义网址、default 是网页那张的地址
        - ``place``：本地图的所在位置（仅 local / mixed 选中本地图时）
        - ``css_url``：可直接写进 CSS 的地址
    """
    r = rng or random

    images = list_images()
    urls = get_background_urls()

    # 两类都有：混合随机。原实现是本地完全压过网址，用户填的网址等于白填。
    if images and urls:
        if r.random() < len(images) / (len(images) + len(urls)):
            hit = r.choice(images)
            return {
                'source': SOURCE_MIXED,
                'value': hit['name'],
                'place': hit['place'],
                'css_url': local_image_url(hit['name'], hit['place']),
            }
        url = r.choice(urls)
        return {'source': SOURCE_MIXED, 'value': url,
                'place': None, 'css_url': url, 'resolved': False}

    if images:
        hit = r.choice(images)
        return {
            'source': SOURCE_LOCAL,
            'value': hit['name'],
            'place': hit['place'],
            'css_url': local_image_url(hit['name'], hit['place']),
        }

    if urls:
        url = r.choice(urls)
        return {'source': SOURCE_REMOTE, 'value': url,
                'place': None, 'css_url': url, 'resolved': False}

    url = default_background_url()
    return {'source': SOURCE_DEFAULT, 'value': url,
            'place': None, 'css_url': url, 'resolved': False}
def overlay_gradient(theme_source: Optional[str] = None) -> str:
    """从主题 CSS 里解析压在背景图上的渐变（没有则返回空串）。

    高级黑在 body 上叠了层深色渐变把背景压暗。自定义背景直接覆盖
    ``background-image`` 会把那层渐变一起挤掉，高级黑下背景会突然变亮，
    所以必须把它拼回来。

    从主题文件里读而不是另存一份：渐变值只存在一处，上游改了也不会漂移。

    Args:
        theme_source: 主题 CSS 文本；``None`` 时按当前主题自动取。

    Returns:
        渐变表达式（如 ``linear-gradient(...)``），无则空串。
    """
    if theme_source is None:
        try:
            from module.webui.app_shell import theme_css_source
        except ImportError:  # pragma: no cover - 未初始化时降级
            return ''
        theme_source = theme_css_source()
    match = re.search(r'--alas-bg-overlay:\s*([^;]+);', theme_source or '')
    return match.group(1).strip() if match else ''


def background_css(choice: Optional[dict] = None,
                   theme_source: Optional[str] = None) -> str:
    """生成覆盖背景图的 CSS 规则。

    背景画在 ``body`` 上（见主题 CSS），这里直接覆盖该属性而不是去改
    ``--alas-apple-bg-image``：亮色主题用 ``url("...")``、暗色覆盖文件用
    ``url(...)``，改令牌得跟随引号写法；改属性则两种主题一致。

    渐变必须写成字面量、不能用 ``var()``：实测在 ``background-image`` 里
    带变量的整条声明会被判无效，算出来是 ``none``（背景根本不显示）。

    Returns:
        CSS 文本；没有背景图时返回空串（不干扰 CSS 原配置）。
    """
    c = choice or pick_background()
    url = c.get('css_url')
    if not url:
        return ''
    gradient = overlay_gradient(theme_source)
    prefix = f'{gradient}, ' if gradient else ''
    return (
        'body{'
        f'background-image:{prefix}url("{url}")!important;'
        '}'
    )


def background_css_file(choice: Optional[dict] = None) -> Optional[Path]:
    """把覆盖 CSS 写成一个文件，供 :func:`utils.add_css_files` 注入。

    注入必须走 ``add_css_files``：它把样式挂到 ``<head>`` 并按 id 记录，
    主题切换时会被 ``_reload_theme_css`` 一并清理重放。
    直接 put_html 到文档里不会进 ``<head>``，选择器根本不生效。

    文件落在 ``config/``（被 gitignore 覆盖），所以启动器同步上游不会碰它。

    抽签结果按会话缓存：一张图在一次会话里稳定，主题切换不会重新抽。
    没有缓存时抽一次并记住，因此刷新页面会换一张。

    Returns:
        写入的路径；没有背景图时返回 ``None``。
    """
    from module.webui.app_dependencies import local
    from module.webui.app_shell import theme_css_source

    theme_source = theme_css_source()
    if choice is None:
        cached = getattr(local, 'webui_bg_choice', None)
        # 主题变了就得重写：压在背景上的渐变是按主题给的（高级黑带、高级白不带）
        if cached is not None and \
                cached.get('theme_source') == theme_source:
            choice = cached
        else:
            choice = pick_background()
            choice['theme_source'] = theme_source
            local.webui_bg_choice = choice

    # 写 CSS 前锁定到具体一张图：浏览器只会请求这个确定地址，不再随机
    if not choice.get('resolved'):
        _lock_remote(choice)
        choice['resolved'] = True
    css = background_css(choice, theme_source)
    path = _PROJECT_ROOT / 'config' / 'webui_bg.css'
    # 选中的本地图可能已被删（用户清理、测试残留等）：继续用它会写出
    # 一条指向不存在文件的规则，背景直接空白。这种情况回落成「没有自定义
    # 背景」，主题自带的那张照常显示。
    if css and choice.get('source') == SOURCE_LOCAL:
        name = choice.get('value')
        place = choice.get('place') or PLACE_ROOT
        if safe_local_path(name, place) is None:
            logger.warning(
                f'[WebUI-背景] 选中的本地图已不存在，回落到默认背景: '
                f'{name} ({place})')
            css = ''
    if not css:
        # 没有背景图了就把旧文件删掉：留着虽不会被注入（调用方拿不到
        # 路径），但磁盘上躺一份过期样式容易让人误判。
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(css, encoding='utf-8')
    except OSError as e:
        logger.warning(f'[WebUI-背景] 写入 {path} 失败: {e}')
        return None
    return path


def session_choice() -> Optional[dict]:
    """取本会话已抽定的背景选择（供界面展示与【提取】）。"""
    from module.webui.app_dependencies import local

    return getattr(local, 'webui_bg_choice', None)


def current_direct_url(choice: Optional[dict] = None) -> str:
    """当前这张背景图的直链。

    网页来源在写 CSS 前已经锁定（见 :func:`_lock_remote`），这里直接取用，
    **不再另发请求**——再请求一次随机接口会拿到另一张图，直链就跟眼前
    显示的对不上了。

    Args:
        choice: 选择结果；``None`` 时取本会话已抽定的。

    Returns:
        直链；无法确定时返回空串。
    """
    c = choice or session_choice() or pick_background()
    source = c.get('source')
    value = c.get('value')
    if source == SOURCE_LOCAL:
        return local_image_url(value, c.get('place') or PLACE_ROOT) if value else ''
    if not c.get('resolved'):
        _lock_remote(c)
        c['resolved'] = True
    return c.get('css_url') or ''


def image_filename(url: str, content_type: str = '') -> str:
    """给下载的图片起个稳定文件名（同一张图每次算出来都一样）。

    用直链的 SHA1 前 16 位：随机图接口每次请求都给新地址，靠 URL 就能
    区分不同图；同一张图重复提取会得到同一个名字，从而实现去重。

    尽量保留原扩展名，拿不到就按 Content-Type 推。
    """
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix not in BACKGROUND_EXTENSIONS:
        suffix = {
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
            'image/bmp': '.bmp',
        }.get(content_type.split(';')[0].strip().lower(), '.jpg')
    digest = hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]
    return f'{digest}{suffix}'


def extract_to_local(url: str, timeout: float = 20.0) -> dict:
    """把一张图下载到 ``bg/提取/``。

    已存在同一张图（同名）时不重复下载。用户自己放进 ``bg/`` 根目录的图
    不会被这里碰到——两条路径互不干扰，所以不存在「把用户的图又提取一遍」。

    Args:
        url: 图片直链。
        timeout: 下载超时（秒）。

    Returns:
        ``{'ok': bool, 'path': str, 'name': str, 'skipped': bool,
        'error': str}``。
    """
    if not url:
        return {'ok': False, 'path': '', 'name': '', 'skipped': False,
                'error': t('Gui.Stat.ExtractNoImageData')}

    try:
        import requests
    except ImportError as e:  # pragma: no cover
        return {'ok': False, 'path': '', 'name': '', 'skipped': False,
                'error': f'requests 不可用: {e}'}

    try:
        resp = requests.get(url, timeout=timeout, stream=True,
                            allow_redirects=True)
        resp.raise_for_status()
        name = image_filename(resp.url or url,
                              resp.headers.get('Content-Type', ''))
        ensure_background_dir()
        target = extracted_dir() / name
        if target.exists():
            resp.close()
            return {'ok': True, 'path': str(target), 'name': name,
                    'skipped': True, 'error': ''}
        # 先写临时文件再改名，避免下载中断留下半张图被当成正常图用
        tmp = target.with_suffix(target.suffix + '.part')
        with open(tmp, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        resp.close()
        tmp.replace(target)
        logger.info(f'[WebUI-背景] 已提取到 {target}')
        return {'ok': True, 'path': str(target), 'name': name,
                'skipped': False, 'error': ''}
    except Exception as e:  # noqa: BLE001 - 网络/IO 问题统一降级报告
        logger.warning(f'[WebUI-背景] 提取失败 {url}: {e}')
        return {'ok': False, 'path': '', 'name': '', 'skipped': False,
                'error': str(e)}


def safe_local_path(name: str, place: str = PLACE_ROOT) -> Optional[Path]:
    """把请求里的文件名解析成 ``bg/`` 内的真实路径。

    只接受裸文件名：拒绝路径分隔符、``..``、非图片扩展名，
    防止顺着这个路由读到目录外的文件。

    Args:
        name: 文件名。
        place: ``root``（``bg/``）或 ``extracted``（``bg/提取/``）。

    Returns:
        合法时返回路径，否则 ``None``。
    """
    if not name or len(name) > 255:
        return None
    if '/' in name or '\\' in name or name in ('.', '..'):
        return None
    if Path(name).suffix.lower() not in BACKGROUND_EXTENSIONS:
        return None

    base = extracted_dir() if place == PLACE_EXTRACTED else background_dir()
    target = base / name
    try:
        # 再确认一次解析结果仍在目标目录内（防符号链接等绕过）
        if target.resolve().parent != base.resolve():
            return None
    except OSError:
        return None
    return target if target.is_file() else None
