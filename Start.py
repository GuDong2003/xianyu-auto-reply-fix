"""项目启动入口：

1. 创建 CookieManager，按配置文件 / 环境变量初始化账号任务
2. 在后台线程启动 FastAPI (reply_server) 提供管理与自动回复接口
3. 主协程保持运行
"""

import os
import sys
import shutil
from pathlib import Path

# 设置标准输出编码为UTF-8（Windows兼容）
def _setup_console_encoding():
    """设置控制台编码为UTF-8，避免Windows GBK编码问题"""
    if sys.platform == 'win32':
        try:
            # 方法1: 设置环境变量
            os.environ['PYTHONIOENCODING'] = 'utf-8'
            
            # 方法2: 尝试设置控制台代码页为UTF-8
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleOutputCP(65001)  # UTF-8代码页
            except Exception:
                pass
            
            # 方法3: 重新包装stdout和stderr
            try:
                if hasattr(sys.stdout, 'buffer'):
                    import io
                    # 只在编码不是UTF-8时重新包装
                    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
                        sys.stdout = io.TextIOWrapper(
                            sys.stdout.buffer, 
                            encoding='utf-8', 
                            errors='replace',
                            line_buffering=True
                        )
                    if sys.stderr.encoding and sys.stderr.encoding.lower() not in ('utf-8', 'utf8'):
                        sys.stderr = io.TextIOWrapper(
                            sys.stderr.buffer, 
                            encoding='utf-8', 
                            errors='replace',
                            line_buffering=True
                        )
            except Exception:
                pass
        except Exception:
            pass

# 在程序启动时设置编码
_setup_console_encoding()

# 定义ASCII安全字符（备用方案）
_OK = '[OK]'
_WARN = '[WARN]'
_ERROR = '[ERROR]'
_INFO = '[INFO]'

# ==================== 在导入任何模块之前先迁移数据库 ====================
def _migrate_database_files_early():
    """在启动前检查并迁移数据库文件到data目录（使用print，因为logger还未初始化）"""
    print("检查数据库文件位置...")
    
    # 确保data目录存在
    data_dir = Path("data")
    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"{_OK} 创建 data 目录")
    
    # 定义需要迁移的文件
    files_to_migrate = [
        ("xianyu_data.db", "data/xianyu_data.db", "主数据库"),
        ("user_stats.db", "data/user_stats.db", "统计数据库"),
    ]
    
    migrated_files = []
    
    # 迁移主数据库和统计数据库
    for old_path, new_path, description in files_to_migrate:
        old_file = Path(old_path)
        new_file = Path(new_path)
        
        if old_file.exists():
            if not new_file.exists():
                # 新位置不存在，移动文件
                try:
                    shutil.move(str(old_file), str(new_file))
                    print(f"{_OK} 迁移{description}: {old_path} -> {new_path}")
                    migrated_files.append(description)
                except Exception as e:
                    print(f"{_WARN} 无法迁移{description}: {e}")
                    print(f"  尝试复制文件...")
                    try:
                        shutil.copy2(str(old_file), str(new_file))
                        print(f"{_OK} 已复制{description}到新位置")
                        print(f"  请在确认数据正常后手动删除: {old_path}")
                        migrated_files.append(f"{description}(已复制)")
                    except Exception as e2:
                        print(f"{_ERROR} 复制{description}失败: {e2}")
            else:
                # 新位置已存在，检查旧文件大小
                try:
                    if old_file.stat().st_size > 0:
                        print(f"{_WARN} 发现旧{description}文件: {old_path}")
                        print(f"  新数据库位于: {new_path}")
                        print(f"  建议备份后删除旧文件")
                except:
                    pass
    
    # 迁移备份文件
    backup_files = list(Path(".").glob("xianyu_data_backup_*.db"))
    if backup_files:
        print(f"发现 {len(backup_files)} 个备份文件")
        backup_migrated = 0
        for backup_file in backup_files:
            new_backup_path = data_dir / backup_file.name
            if not new_backup_path.exists():
                try:
                    shutil.move(str(backup_file), str(new_backup_path))
                    print(f"{_OK} 迁移备份文件: {backup_file.name}")
                    backup_migrated += 1
                except Exception as e:
                    print(f"{_WARN} 无法迁移备份文件 {backup_file.name}: {e}")
        
        if backup_migrated > 0:
            migrated_files.append(f"{backup_migrated}个备份文件")
    
    # 输出迁移总结
    if migrated_files:
        print(f"{_OK} 数据库迁移完成，已迁移: {', '.join(migrated_files)}")
    else:
        print(f"{_OK} 数据库文件检查完成")
    
    return True

# 在导入 db_manager 之前先执行数据库迁移
try:
    _migrate_database_files_early()
except Exception as e:
    print(f"{_WARN} 数据库迁移检查失败: {e}")
    # 继续启动，因为可能是首次运行

# ==================== 检查并安装Playwright浏览器 ====================
def _check_and_install_playwright():
    """检查Playwright浏览器是否存在，如果不存在则自动安装"""
    print("检查Playwright浏览器...")

    if os.getenv("XIANYU_PRODUCTION", "").strip().lower() == "true":
        from xianyu_control.browser_runtime import (
            ProductionBrowserUnavailable,
            resolve_production_chromium_executable,
        )

        try:
            executable = resolve_production_chromium_executable()
        except ProductionBrowserUnavailable as exc:
            print(f"{_WARN} {exc}")
            return False
        print(f"{_OK} 找到生产镜像内的Playwright浏览器: {executable}")
        return True
    
    # 检查是否安装了playwright模块
    try:
        import playwright
    except ImportError:
        print(f"{_WARN} Playwright模块未安装，跳过浏览器检查")
        return False
    
    # 检查Playwright浏览器是否存在
    playwright_installed = False
    possible_paths = []
    
    # 如果是打包后的exe，优先检查exe同目录
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        playwright_dir = exe_dir / 'playwright'
        possible_paths.insert(0, playwright_dir)  # 插入到最前面，优先检查
        
        # 检查exe同目录的浏览器是否完整
        if playwright_dir.exists():
            chromium_dirs = list(playwright_dir.glob('chromium-*'))
            if chromium_dirs:
                chromium_dir = chromium_dirs[0]
                chrome_exe = chromium_dir / 'chrome-win' / 'chrome.exe'
                if chrome_exe.exists() and chrome_exe.stat().st_size > 0:
                    print(f"{_OK} 找到已提取的Playwright浏览器: {chrome_exe}")
                    print(f"{_INFO} 浏览器版本: {chromium_dir.name}")
                    # 清除可能存在的旧环境变量，使用实际存在的浏览器
                    if 'PLAYWRIGHT_BROWSERS_PATH' in os.environ:
                        old_path = os.environ['PLAYWRIGHT_BROWSERS_PATH']
                        if old_path != str(playwright_dir):
                            print(f"{_INFO} 清除旧的环境变量: {old_path}")
                            del os.environ['PLAYWRIGHT_BROWSERS_PATH']
                    # 确保环境变量已设置
                    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(playwright_dir)
                    print(f"{_INFO} 已设置PLAYWRIGHT_BROWSERS_PATH: {playwright_dir}")
                    playwright_installed = True
                    return True
    
    # Windows上的常见位置
    if sys.platform == 'win32':
        # 用户缓存目录
        user_cache = Path.home() / '.cache' / 'ms-playwright'
        possible_paths.append(user_cache)
        
        # LocalAppData目录
        local_appdata = os.getenv('LOCALAPPDATA')
        if local_appdata:
            possible_paths.append(Path(local_appdata) / 'ms-playwright')
        
        # AppData目录
        appdata = os.getenv('APPDATA')
        if appdata:
            possible_paths.append(Path(appdata) / 'ms-playwright')
    
    # 检查是否存在chromium浏览器
    for path in possible_paths:
        if path.exists():
            # 查找chromium目录
            chromium_dirs = list(path.glob('chromium-*'))
            if chromium_dirs:
                for chromium_dir in chromium_dirs:
                    chrome_win = chromium_dir / 'chrome-win'
                    chrome_exe = chrome_win / 'chrome.exe'
                    if chrome_exe.exists():
                        print(f"{_OK} 找到Playwright浏览器: {chrome_exe}")
                        # 设置环境变量
                        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(path)
                        playwright_installed = True
                        break
                if playwright_installed:
                    break
    
    # 如果没找到，尝试使用playwright命令检查
    if not playwright_installed:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(headless=True)
                    browser.close()
                    print(f"{_OK} Playwright浏览器已安装（通过API检测）")
                    playwright_installed = True
                except Exception:
                    pass
        except Exception:
            pass
    
    # 如果没找到，先尝试从临时目录提取（如果是打包的exe）
    if not playwright_installed and getattr(sys, 'frozen', False):
        try:
            exe_dir = Path(sys.executable).parent
            playwright_dir = exe_dir / 'playwright'
            
            if hasattr(sys, '_MEIPASS'):
                temp_dir = Path(sys._MEIPASS)
                temp_playwright = temp_dir / 'playwright'
                
                if temp_playwright.exists():
                    # 查找所有 chromium 相关目录（包括 chromium-* 和 chromium_headless_shell-*）
                    temp_chromium_dirs = list(temp_playwright.glob('chromium*'))
                    if temp_chromium_dirs:
                        print(f"{_INFO} 检测到打包的浏览器文件，正在提取...")
                        playwright_dir.mkdir(parents=True, exist_ok=True)
                        extracted_count = 0
                        
                        for temp_chromium_dir in temp_chromium_dirs:
                            temp_chrome_win = temp_chromium_dir / 'chrome-win'
                            
                            # 检查完整版或 headless_shell 版
                            temp_chrome_exe = temp_chrome_win / 'chrome.exe'
                            temp_headless_exe = temp_chrome_win / 'headless_shell.exe'
                            
                            # 验证文件是否存在
                            is_valid = False
                            if temp_chromium_dir.name.startswith('chromium_headless_shell'):
                                is_valid = temp_headless_exe.exists() and temp_headless_exe.stat().st_size > 0
                            else:
                                is_valid = temp_chrome_exe.exists() and temp_chrome_exe.stat().st_size > 0
                            
                            if is_valid:
                                target_chromium_dir = playwright_dir / temp_chromium_dir.name
                                
                                if not target_chromium_dir.exists():
                                    try:
                                        shutil.copytree(temp_chromium_dir, target_chromium_dir, dirs_exist_ok=True)
                                        
                                        # 验证提取的文件
                                        if temp_chromium_dir.name.startswith('chromium_headless_shell'):
                                            target_exe = target_chromium_dir / 'chrome-win' / 'headless_shell.exe'
                                        else:
                                            target_exe = target_chromium_dir / 'chrome-win' / 'chrome.exe'
                                        
                                        if target_exe.exists() and target_exe.stat().st_size > 0:
                                            print(f"{_OK} 浏览器文件提取成功: {target_exe}")
                                            print(f"{_INFO} 浏览器版本: {temp_chromium_dir.name}")
                                            extracted_count += 1
                                    except Exception as e:
                                        print(f"{_WARN} 提取 {temp_chromium_dir.name} 失败: {e}")
                        
                        if extracted_count > 0:
                            # 清除可能存在的旧环境变量
                            if 'PLAYWRIGHT_BROWSERS_PATH' in os.environ:
                                old_path = os.environ['PLAYWRIGHT_BROWSERS_PATH']
                                print(f"{_INFO} 清除旧的环境变量: {old_path}")
                                del os.environ['PLAYWRIGHT_BROWSERS_PATH']
                            # 设置新的环境变量
                            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(playwright_dir)
                            print(f"{_INFO} 已提取 {extracted_count} 个浏览器版本")
                            print(f"{_INFO} 已设置PLAYWRIGHT_BROWSERS_PATH: {playwright_dir}")
                            playwright_installed = True
                            return True
        except Exception as e:
            print(f"{_WARN} 提取浏览器文件时出错: {e}")
    
    # 如果没找到，尝试安装
    if not playwright_installed:
        print(f"{_WARN} 未找到Playwright浏览器，正在自动安装...")
        print("   这可能需要几分钟时间，请耐心等待...")
        
        try:
            # 方法1: 尝试使用playwright的Python API安装（推荐，适用于打包后的exe）
            try:
                # 直接调用playwright的安装函数
                from playwright._impl._driver import install_driver, install_browsers
                print("   正在安装Playwright驱动...")
                install_driver()
                print("   正在安装Chromium浏览器...")
                install_browsers(['chromium'])
                print(f"{_OK} Playwright浏览器安装成功（通过API）")
                playwright_installed = True
            except ImportError:
                # 如果API不可用，使用命令行方式
                print("   使用命令行方式安装...")
                import subprocess
                
                # 尝试使用playwright的安装命令
                # 对于打包后的exe，playwright模块应该已经包含
                creation_flags = 0
                if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    creation_flags = subprocess.CREATE_NO_WINDOW
                
                result = subprocess.run(
                    [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10分钟超时
                    creationflags=creation_flags
                )
                
                if result.returncode == 0:
                    print(f"{_OK} Playwright浏览器安装成功")
                    playwright_installed = True
                else:
                    print(f"{_WARN} Playwright浏览器安装失败")
                    if result.stdout:
                        print(f"   输出: {result.stdout[-500:]}")  # 只显示最后500字符
                    if result.stderr:
                        print(f"   错误: {result.stderr[-500:]}")
                    print("   您可以稍后手动运行: playwright install chromium")
                    return False
            except Exception as api_error:
                # API安装失败，尝试命令行方式
                print(f"   API安装失败，尝试命令行方式: {api_error}")
                import subprocess
                
                creation_flags = 0
                if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    creation_flags = subprocess.CREATE_NO_WINDOW
                
                result = subprocess.run(
                    [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    creationflags=creation_flags
                )
                
                if result.returncode == 0:
                    print(f"{_OK} Playwright浏览器安装成功（通过命令行）")
                    playwright_installed = True
                else:
                    print(f"{_WARN} Playwright浏览器安装失败")
                    if result.stdout:
                        print(f"   输出: {result.stdout[-500:]}")
                    if result.stderr:
                        print(f"   错误: {result.stderr[-500:]}")
                    print("   您可以稍后手动运行: playwright install chromium")
                    return False
            except ImportError:
                # 如果playwright模块不可用，尝试使用subprocess
                import subprocess
                result = subprocess.run(
                    [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                    capture_output=True,
                    text=True,
                    timeout=600,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                
                if result.returncode == 0:
                    print(f"{_OK} Playwright浏览器安装成功")
                    playwright_installed = True
                else:
                    print(f"{_WARN} Playwright浏览器安装失败")
                    if result.stdout:
                        print(f"   输出: {result.stdout}")
                    if result.stderr:
                        print(f"   错误: {result.stderr}")
                    print("   您可以稍后手动运行: playwright install chromium")
                    return False
                
        except subprocess.TimeoutExpired:
            print(f"{_WARN} Playwright浏览器安装超时（超过10分钟）")
            print("   您可以稍后手动运行: playwright install chromium")
            return False
        except Exception as e:
            print(f"{_WARN} Playwright浏览器安装失败: {e}")
            import traceback
            print(f"   详细错误: {traceback.format_exc()}")
            print("   您可以稍后手动运行: playwright install chromium")
            return False
    
    return playwright_installed

# 检查并安装Playwright浏览器
try:
    _check_and_install_playwright()
except Exception as e:
    print(f"{_WARN} Playwright浏览器检查失败: {e}")
    print("   程序将继续启动，但Playwright功能可能不可用")
    # 继续启动，不影响主程序运行

# ==================== 现在可以安全地导入其他模块 ====================
import asyncio
import threading
import uvicorn
from urllib.parse import urlparse
from loguru import logger

# 修复Linux环境下的asyncio子进程问题
if sys.platform.startswith('linux'):
    try:
        # 在程序启动时就设置正确的事件循环策略
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        logger.debug("已设置事件循环策略以支持子进程")
    except Exception as e:
        logger.debug(f"设置事件循环策略失败: {e}")

from config import AUTO_REPLY, COOKIES_LIST
import cookie_manager as cm
from db_manager import db_manager
from file_log_collector import setup_file_logging


def _apply_production_security() -> None:
    if os.getenv('XIANYU_PRODUCTION', 'false').lower() != 'true':
        return
    admin_password = os.getenv('ADMIN_PASSWORD', '')
    jwt_secret = os.getenv('JWT_SECRET_KEY', '')
    if len(admin_password) < 16 or admin_password == 'admin123':
        raise RuntimeError('生产环境 ADMIN_PASSWORD 必须至少 16 位且不能使用默认值')
    if len(jwt_secret) < 32 or jwt_secret == 'default-secret-key':
        raise RuntimeError('生产环境 JWT_SECRET_KEY 必须至少 32 位且不能使用默认值')
    if db_manager.verify_user_password('admin', 'admin123'):
        if not db_manager.update_user_password('admin', admin_password):
            raise RuntimeError('默认管理员密码轮换失败')
        logger.warning('默认管理员密码已在生产启动时完成轮换')


def _start_api_server():
    """后台线程启动 FastAPI 服务"""
    api_conf = AUTO_REPLY.get('api', {})

    # 优先使用环境变量配置
    host = os.getenv('API_HOST', '0.0.0.0')  # 默认绑定所有接口
    port = int(os.getenv('API_PORT', '8090'))  # 默认端口8090

    # 如果配置文件中有特定配置，则使用配置文件
    if 'host' in api_conf:
        host = api_conf['host']
    if 'port' in api_conf:
        port = api_conf['port']

    # 兼容旧的URL配置方式
    if 'url' in api_conf and 'host' not in api_conf and 'port' not in api_conf:
        url = api_conf.get('url', 'http://0.0.0.0:8090/xianyu/reply')
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname != 'localhost':
            host = parsed.hostname
        port = parsed.port or 8090

    logger.info(f"启动Web服务器: http://{host}:{port}")
    # 在后台线程中创建独立事件循环并直接运行 server.serve()
    import uvicorn
    try:
        config = uvicorn.Config("reply_server:app", host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())
    except Exception as e:
        logger.error(f"uvicorn服务器启动失败: {e}")
        try:
            # 确保线程内事件循环被正确关闭
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.stop()
        except Exception:
            pass




def load_keywords_file(path: str):
    """从文件读取关键字 -> [(keyword, reply)]"""
    kw_list = []
    p = Path(path)
    if not p.exists():
        return kw_list
    with p.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '\t' in line:
                k, r = line.split('\t', 1)
            elif ' ' in line:
                k, r = line.split(' ', 1)
            elif ':' in line:
                k, r = line.split(':', 1)
            else:
                continue
            kw_list.append((k.strip(), r.strip()))
    return kw_list


async def main():
    print("开始启动主程序...")

    # 初始化文件日志收集器
    print("初始化文件日志收集器...")
    setup_file_logging()
    logger.info("文件日志收集器已启动，开始收集实时日志")

    loop = asyncio.get_running_loop()

    # 创建 CookieManager 并在全局暴露
    print("创建 CookieManager...")
    cm.manager = cm.CookieManager(loop)
    manager = cm.manager
    print("CookieManager 创建完成")
    _apply_production_security()
    external_connector = os.getenv('XIANYU_EXTERNAL_CONNECTOR', 'false').lower() == 'true'

    # 1) 从数据库加载的 Cookie 已经在 CookieManager 初始化时完成
    # 为每个启用的 Cookie 启动任务
    for cid, val in (() if external_connector else manager.cookies.items()):
        # 检查账号是否启用
        if not manager.get_cookie_status(cid):
            logger.info(f"跳过禁用的 Cookie: {cid}")
            continue

        try:
            # 直接启动任务，不重新保存到数据库
            from db_manager import db_manager
            logger.info(f"正在获取Cookie详细信息: {cid}")
            cookie_info = db_manager.get_cookie_details(cid)
            user_id = cookie_info.get('user_id') if cookie_info else None
            logger.info(f"Cookie详细信息获取成功: {cid}, user_id: {user_id}")

            logger.info(f"正在创建异步任务: {cid}")
            task = loop.create_task(manager._run_xianyu(cid, val, user_id))
            manager.tasks[cid] = task
            logger.info(f"启动数据库中的 Cookie 任务: {cid} (用户ID: {user_id})")
            logger.info(f"任务已添加到管理器，当前任务数: {len(manager.tasks)}")
        except Exception as e:
            logger.error(f"启动 Cookie 任务失败: {cid}, {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
    
    # 2) 如果配置文件中有新的 Cookie，也加载它们
    for entry in COOKIES_LIST:
        cid = entry.get('id')
        val = entry.get('value')
        if not cid or not val or cid in manager.cookies:
            continue
        
        kw_file = entry.get('keywords_file')
        kw_list = load_keywords_file(kw_file) if kw_file else None
        manager.add_cookie(cid, val, kw_list)
        logger.info(f"从配置文件加载 Cookie: {cid}")

    # 3) 若老环境变量仍提供单账号 Cookie，则作为 default 账号
    env_cookie = os.getenv('COOKIES_STR')
    if env_cookie and 'default' not in manager.list_cookies():
        manager.add_cookie('default', env_cookie)
        logger.info("从环境变量加载 default Cookie")

    if external_connector:
        logger.info("独立连接器模式已启用，控制平面不会创建闲鱼 WebSocket 或浏览器任务")

    # 启动 API 服务线程
    print("启动 API 服务线程...")
    threading.Thread(target=_start_api_server, daemon=True).start()
    print("API 服务线程已启动")

    # 阻塞保持运行
    print("主程序启动完成，保持运行...")
    await asyncio.Event().wait()


if __name__ == '__main__':
    # 避免使用被monkey patch的asyncio.run()
    # 使用原生的事件循环管理方式
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()
