"""API 客户端模块。

负责获取公告，
配置自有 API 地址后才查询公告。
"""
from typing import Any, Dict, List, Tuple, Optional

from module.base import http

from deploy.api_endpoints import API_BASE_URL
from module.logger import logger


class ApiClient:
    """统一的 API 客户端，保留主备端点配置。"""
    
    # 主域名和备用域名列表
    PRIMARY_DOMAIN = API_BASE_URL
    FALLBACK_DOMAIN = API_BASE_URL
    
    # API端点路径
    ANNOUNCEMENT_PATH = '/api/v2/get/notice/data.json'

    # 公告检查间隔（秒），1.5分钟 = 90秒
    ANNOUNCEMENT_CHECK_INTERVAL = 90
    
    @classmethod
    def _get_endpoints(cls, path: str) -> List[str]:
        """获取指定路径的所有端点 URL（主域名 + 备用域名）。
        
        Args:
            path (str): API 相对路径。
            
        Returns:
            list[str]: 端点完整 URL 列表。
        """
        return list(dict.fromkeys(
            f'{domain}{path}' for domain in (cls.PRIMARY_DOMAIN, cls.FALLBACK_DOMAIN) if domain
        ))
    
    @classmethod
    def _get_with_fallback(cls, path: str, params: Dict[str, Any] = None, timeout: int = 10,
                           success_codes: List[int] = None) -> Tuple[bool, int, str]:
        """发送 GET 请求，支持主备域名故障转移。

        Args:
            path (str): API 相对路径。
            params (dict[str, Any] | None): URL 查询参数。
            timeout (int): 请求超时秒数。
            success_codes (list[int] | None): 视为成功的状态码列表，默认为 [200]。

        Returns:
            tuple[bool, int, str]: (是否成功, HTTP 状态码, 响应文本或错误信息)。
        """
        if success_codes is None:
            success_codes = [200]
            
        endpoints = cls._get_endpoints(path)
        last_error = None
        last_status = 0
        
        for i, endpoint in enumerate(endpoints):
            try:
                domain_type = "主域名" if i == 0 else "备用域名"
                logger.debug(f'[基础-API] 尝试使用{domain_type}: {endpoint}')
                
                response = http.get(
                    endpoint,
                    params=params,
                    timeout=timeout,
                    headers={'User-Agent': http.USER_AGENT}
                )

                if response.status_code in success_codes:
                    if i > 0:
                        logger.info(f'[基础-API] 使用{domain_type}请求成功')
                    return True, response.status_code, response.text
                else:
                    logger.warning(f'[基础-API] {domain_type}返回错误状态: {response.status_code}')
                    last_error = f'HTTP {response.status_code}'
                    last_status = response.status_code
                    if 400 <= last_status < 500 and last_status != 429:
                        break
                    
            except http.exceptions.Timeout:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}请求超时')
                last_error = 'Timeout'
            except http.exceptions.RequestException as e:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}请求失败: {e}')
                last_error = str(e)
            except Exception as e:
                logger.warning(f'[基础-API] {domain_type if i > 0 else "主域名"}发生异常: {e}')
                last_error = str(e)
        
        return False, last_status, last_error or 'Unknown error'
    
    @classmethod
    def get_announcement(cls, timeout: int = 1, current_id: int = None) -> Optional[Dict[str, Any]]:
        """获取公告信息（同步）。
        
        Args:
            timeout (int): 请求超时时间（秒），默认为 1 秒。
            current_id (int | None): 当前已知公告 ID，用于增量检查。
            
        Returns:
            dict[str, Any] | None: 公告数据字典；若无更新或获取失败则返回 None。
        """
        if not cls.PRIMARY_DOMAIN and not cls.FALLBACK_DOMAIN:
            return None
        import time
        try:
            # 添加时间戳参数以绕过缓存
            timestamp = int(time.time())
            params = {'t': timestamp}
            if current_id is not None:
                params['id'] = current_id            
            # 允许 200 (OK) 和 304 (Not Modified)
            success, status_code, response_text = cls._get_with_fallback(
                cls.ANNOUNCEMENT_PATH,
                params=params,
                timeout=timeout,
                success_codes=[200, 304]
            )            
            if success:
                # 304 或空内容表示无更新
                if status_code == 304 or not response_text.strip():
                    return None
                    
                import json
                try:
                    data = json.loads(response_text)

                    if not isinstance(data, dict) or data.get('schema_version') != 2 or 'announcement' not in data:
                        logger.warning('[基础-API] 公告文档格式不正确')
                        return None
                    notice = data['announcement']
                    if notice is None:
                        return {}  # Explicit withdrawal; distinct from a failed request.
                    if not isinstance(notice, dict) or not notice.get('id'):
                        return None
                    if str(notice['id']) == str(current_id):
                        return None
                    data = {**notice, 'announcementId': notice['id']}
                    
                    # 如果返回空字典或无ID，也视为无更新
                    if not data or not data.get('announcementId'):
                        logger.info('[Base] 公告数据为空或无ID')
                        return None
                        
                    # 只要有标题，且有内容 OR 链接，就是有效公告
                    if data.get('title') and (data.get('content') or data.get('url')):
                        return data
                    else:
                        return None
                except json.JSONDecodeError as e:
                    logger.warning(f'[Base] 解析公告JSON失败: {e}, response={response_text[:100]}')
                    return None
            else:
                logger.warning(f'[Base] 获取公告失败: {response_text}')
                return None
                
        except Exception as e:
            logger.warning(f'[Base] 获取公告异常: {e}')
            return None

