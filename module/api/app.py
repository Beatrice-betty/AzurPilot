"""Starlette 应用工厂：静态 React 页面与同源 WebSocket。"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from module.api.background_service import LIBRARY_DIR, gallery_add_bytes, proxy_fetch
from module.api.config_service import ConfigService, ROOT
from module.api.router import Router
from module.api.runtime_service import RuntimeService
from module.api.socket import Gateway
from module.api.static import FrontendFiles
from module.logger import logger
from module.runtime.password_utils import ensure_password_for_host, is_demo_mode
from module.runtime.setting import State


def create_app(*, root: Path = ROOT, password=None, manage_runtime=True, mount_mcp=True):
    """创建应用；测试可以关闭真实进程生命周期并使用临时配置目录。"""
    configs = ConfigService(root)
    runtime = RuntimeService(configs)
    mcp_app = None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-k', '--key')
    parser.add_argument('--run', nargs='+')
    args, _ = parser.parse_known_args()
    if password is None:
        key = args.key or State.deploy_config.Password
        host = State.webui_host or State.deploy_config.WebuiHost
        password = ensure_password_for_host(key, host, demo=is_demo_mode())
        if password and password != key:
            State.deploy_config.Password = password
    gateway = Gateway(Router(configs, runtime), password)

    @asynccontextmanager
    async def lifespan(application):
        try:
            if manage_runtime:
                from module.api.lifecycle import startup
                from module.runtime.deploy_settings import parse_run_config
                from module.runtime.startup_memory import consume_update_restart, startup_runs
                update_restart = consume_update_restart()
                runs = args.run or parse_run_config(State.deploy_config.Run)
                if not args.run:
                    # --run 是显式清单，不叠加记忆。
                    runs = startup_runs(runs, update_restart=update_restart)
                await asyncio.to_thread(startup, runs)
                if State.deploy_config.DiscordRichPresence:
                    try:
                        from module.runtime.discord_presence import init_discord_rpc
                        init_discord_rpc()
                    except Exception:
                        logger.exception('Discord RPC 启动失败，继续运行 WebUI')
            yield
        finally:
            try:
                # 先禁止 MCP 新操作并等待在途操作结束，再回收共享运行时。
                if mcp_app is not None:
                    await mcp_app.state.tools.close()
            finally:
                if manage_runtime:
                    try:
                        from module.runtime.discord_presence import async_close_discord_rpc
                        await async_close_discord_rpc()
                    except Exception:
                        logger.exception('Discord RPC 清理失败，继续回收共享运行时')
                    finally:
                        from module.api.lifecycle import clearup
                        await asyncio.to_thread(clearup)

    dist = root / 'frontend/dist'

    async def index(request):
        if (dist / 'index.html').is_file():
            return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache'})
        return PlainTextResponse('前端尚未构建，请在 frontend 目录运行 npm ci 和 npm run build。', status_code=503)

    async def health(request):
        return JSONResponse({'status': 'ok', 'protocolVersion': 1})

    async def meowfficer_score_report(request):
        """指挥喵评分报告（自包含 HTML），供浏览器直接打开查看。"""
        path = root / 'log' / 'meowfficer_score.html'
        if not path.is_file():
            return PlainTextResponse('评分报告尚未生成，请先运行「指挥喵评分」任务。', status_code=404)
        return FileResponse(path, media_type='text/html', headers={'Cache-Control': 'no-cache'})

    from module.api.android import routes as android_routes
    async def background_upload(request):
        """接收浏览器上传的本地背景图，存进 cache/background/library（本地图片的唯一落点）。"""
        form = await request.form()
        upload = form.get('file')
        if upload is None or not hasattr(upload, 'read'):
            return JSONResponse({'error': '没有收到文件。'}, status_code=400)
        data = await upload.read()
        try:
            entry = gallery_add_bytes(data, getattr(upload, 'filename', '') or '', getattr(upload, 'content_type', '') or '')
        except Exception as error:
            return JSONResponse({'error': str(error)}, status_code=400)
        return JSONResponse({'entry': entry})

    async def background_media(request):
        """同源代理一张网图：解析出的直链由这里回给浏览器，避免防盗链或跨域让显示的图与直链分叉。"""
        target = request.query_params.get('url', '')
        if not target:
            return JSONResponse({'error': '缺少 url 参数。'}, status_code=400)
        try:
            data, content_type = proxy_fetch(target)
        except Exception as error:
            return JSONResponse({'error': str(error)}, status_code=400)
        return Response(data, media_type=content_type, headers={'Cache-Control': 'no-cache'})

    routes = [Route('/healthz', health),
              Route('/api/v1/background/media', background_media),
              Route('/reports/meowfficer_score', meowfficer_score_report),
              WebSocketRoute('/api/v1/ws', gateway.endpoint)]
    routes.extend(android_routes(configs, runtime))
    # 背景图库：图片直接由 StaticFiles 提供（与 research-items 等模板图同一套做法），
    # 上传走下面那个 POST；目录不存在时先建出来，免得挂载失败。
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    routes.append(Route('/api/v1/background/gallery', background_upload, methods=['POST']))
    routes.append(Mount('/background-library', StaticFiles(directory=LIBRARY_DIR)))
    if (dist / 'assets').is_dir():
        routes.append(Mount('/assets', StaticFiles(directory=dist / 'assets')))
    # 科研掉落的物品图标直接用仓库里的模板图，不走前端构建，
    # 这样补了新模板立刻生效，不用重新 npm build。
    research_items = root / 'assets' / 'stats' / 'research_items'
    if research_items.is_dir():
        routes.append(Mount('/research-items', StaticFiles(directory=research_items)))
    # 大世界掉落的物品图标同理。opsi_reward_items 是模板库的超集
    # （opsi_items 的每个模板名这里都有），挂一个目录就够。
    opsi_items = root / 'assets' / 'stats' / 'opsi_reward_items'
    if opsi_items.is_dir():
        routes.append(Mount('/opsi-items', StaticFiles(directory=opsi_items)))
    if mount_mcp:
        from mcp_server_sse import create_app as create_mcp_app, configure_auth
        configure_auth(password, public_bind=bool(password))
        mcp_app = create_mcp_app(configs, runtime, manage_runtime=False)
        routes.append(Mount('/mcp', mcp_app))
    if (dist / 'index.html').is_file():
        routes.append(Mount('/', FrontendFiles(directory=dist)))
    else:
        routes.append(Route('/{path:path}', index))
    application = Starlette(routes=routes, lifespan=lifespan)
    application.state.gateway = gateway
    return application
