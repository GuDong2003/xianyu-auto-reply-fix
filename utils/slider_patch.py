"""
滑块验证模块补丁
用于在运行时修改 XianyuSliderStealth 的方法，无需重新编译
"""
from typing import Any
from loguru import logger
from datetime import datetime, timedelta
import time
import random


def send_notification(user_id: str, title: str, message: str, notification_type: str = "info"):
    """
    发送通知的公共方法（兼容旧调用，底层转发到统一通知链路）
    """
    try:
        logger.info(f"【{user_id}】准备发送通知: {title}")
        from utils.notification_dispatcher import dispatch_account_notifications_sync

        return dispatch_account_notifications_sync(
            user_id,
            message,
            title=title,
            notification_type=notification_type,
        )
    except Exception as e:
        logger.error(f"【{user_id}】发送通知异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def _handle_slider_verification(page, user_id: str, max_attempts: int = 5) -> bool:
    """
    检测并处理滑块验证（支持有限次数重试）
    
    Args:
        page: Playwright Page 对象
        user_id: 用户ID，用于日志记录
        max_attempts: 最大尝试次数，默认5次
    
    Returns:
        bool: 如果检测到并成功处理返回True，如果没有滑块返回False，如果达到最大尝试次数返回False
    """
    try:
        logger.info(f"【{user_id}】检测滑块验证...")
        
        # 等待页面稳定
        time.sleep(1)
        # 兼容 Frame 或 Page：Frame 没有 mouse 属性，因此为 mouse 操作准备 page 对象
        # Frame 对象有 page 属性，指向其父 Page 对象
        if hasattr(page, 'page') and page.page is not None:
            mouse_page = page.page
        else:
            mouse_page = page
        
        # 检测滑块验证的多种方式
        slider_selectors = [
            '#nc_1_n1z',  # 滑块按钮
            'span[id*="nc_1_n1z"]',  # 滑块按钮变体
            'span.nc-lang-cnt[data-nc-lang="SLIDE"]',
            "xpath=//span[contains(text(), '向右滑动验证')]",
            'text=向右滑动验证',
            '.nc_scale',  # 滑块轨道
            '.nc-wrapper',  # 滑块包装器
            '.nc-iconfont',  # 滑块图标
        ]
        
        slider_element = None
        for selector in slider_selectors:
            try:
                slider_element = page.query_selector(selector)
                if slider_element:
                    logger.info(f"【{user_id}】检测到滑块验证元素: {selector}")
                    break
            except:
                continue
        
        # 检测滑块验证弹窗
        popup_selectors = [
            '.nc-wrapper',  # 滑块包装器（通常在弹窗中）
            'div[class*="nc-wrapper"]',
            'span.nc-lang-cnt[data-nc-lang="SLIDE"]',
            "xpath=//span[contains(text(), '向右滑动验证')]",
            'div:contains("unusual traffic")',
            'div:contains("检测到异常流量")',
        ]
        
        popup_detected = False
        for selector in popup_selectors:
            try:
                popup = page.query_selector(selector)
                if popup:
                    popup_detected = True
                    logger.info(f"【{user_id}】检测到滑块验证弹窗: {selector}")
                    break
            except:
                continue
        
        # 如果没有检测到滑块，返回False
        if not slider_element and not popup_detected:
            logger.info(f"【{user_id}】未检测到滑块验证")
            return False
        
        # 如果检测到滑块，尝试处理（有限次数重试）
        logger.warning(f"【{user_id}】检测到滑块验证，开始处理（最多尝试{max_attempts}次）...")
        
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            logger.info(f"【{user_id}】滑块验证处理尝试 {attempt}/{max_attempts}...")
            
            try:
                # 重新查找滑块元素
                slider_element = None
                for selector in slider_selectors:
                    try:
                        slider_element = page.query_selector(selector)
                        if slider_element:
                            break
                    except:
                        continue
                
                if not slider_element:
                    logger.warning(f"【{user_id}】无法找到滑块元素，可能已消失")
                    time.sleep(2)
                    # 检查是否已经通过验证
                    if not page.query_selector('#nc_1_n1z'):
                        logger.info(f"【{user_id}】滑块验证可能已通过")
                        return True
                    continue
                
                # 获取滑块相关信息
                slider_box = None
                try:
                    slider_box = slider_element.bounding_box()
                    logger.info(f"【{user_id}】滑块位置: {slider_box}")
                except:
                    pass
                
                # 使用 Playwright API 处理滑块
                logger.info(f"【{user_id}】使用 Playwright API 处理滑块...")
                
                # 获取滑块和轨道
                try:
                    track = page.query_selector('.nc_scale')
                    if not track:
                        track = page.query_selector('.nc-wrapper .nc_scale')
                except:
                    track = None
                
                # 计算滑动距离
                distance = 300  # 默认距离
                if track and slider_element:
                    try:
                        track_box = track.bounding_box()
                        if slider_box is None:
                            slider_box = slider_element.bounding_box()
                        
                        if track_box and slider_box:
                            track_width = track_box.get('width', 0)
                            slider_width = slider_box.get('width', 0)
                            
                            if track_width and slider_width:
                                distance = track_width - slider_width
                                logger.info(f"【{user_id}】计算滑动距离: {distance}px (轨道宽度: {track_width}px, 滑块宽度: {slider_width}px)")
                            elif track_width:
                                # 如果只有轨道宽度，使用轨道宽度的80%作为滑动距离
                                distance = int(track_width * 0.8)
                                logger.info(f"【{user_id}】使用轨道宽度的80%作为滑动距离: {distance}px")
                    except Exception as calc_e:
                        logger.warning(f"【{user_id}】计算滑动距离失败: {calc_e}")
                        distance = 300  # 默认距离
                
                # 等待滑块可见
                slider_visible = False
                try:
                    logger.info(f"【{user_id}】等待滑块元素可见...")
                    try:
                        slider_element.wait_for_element_state('visible', timeout=5000)
                        logger.info(f"【{user_id}】✓ 滑块元素已可见")
                        slider_visible = True
                    except Exception as wait_e:
                        logger.warning(f"【{user_id}】等待滑块可见超时: {wait_e}")
                        
                        # 先检查是否已经登录成功
                        logger.info(f"【{user_id}】检查是否已登录成功...")
                        if _check_login_success_by_element(page, user_id):
                            logger.success(f"【{user_id}】✅ 检测到已登录成功，无需继续滑块验证")
                            return True
                        
                        # 如果未登录成功，检查滑块是否真的不可见
                        logger.info(f"【{user_id}】未检测到登录成功，检查滑块可见性...")
                        try:
                            is_visible = slider_element.is_visible()
                            if not is_visible:
                                logger.warning(f"【{user_id}】滑块不可见且未登录成功，刷新页面重试...")
                                
                                # 获取真正的 page 对象（兼容 Frame）
                                # Frame 对象有 page 属性，指向其父 Page 对象
                                if hasattr(page, 'page') and page.page is not None:
                                    real_page = page.page
                                else:
                                    real_page = page
                                
                                logger.debug(f"【{user_id}】page 类型: {type(page).__name__}, real_page 类型: {type(real_page).__name__}")
                                
                                try:
                                    logger.info(f"【{user_id}】刷新浏览器页面（重新加载）...")
                                    logger.info(f"【{user_id}】当前URL: {real_page.url}")
                                    
                                    # 使用 reload() 方法刷新当前页面
                                    real_page.reload(wait_until='domcontentloaded', timeout=30000)
                                    time.sleep(3)
                                    
                                    logger.info(f"【{user_id}】浏览器页面刷新完成")
                                    logger.info(f"【{user_id}】刷新后URL: {real_page.url}")
                                    logger.info(f"【{user_id}】重新开始验证...")
                                    
                                    # 递归调用自身重新验证（减少尝试次数避免无限循环）
                                    return _handle_slider_verification(real_page, user_id, max_attempts=max(5, max_attempts - 2))
                                    
                                except Exception as refresh_e:
                                    logger.error(f"【{user_id}】刷新页面失败: {refresh_e}")
                                    return False
                            else:
                                logger.info(f"【{user_id}】滑块可见，尝试强制显示...")
                                slider_visible = True
                        except:
                            pass
                        
                        # 尝试使用JavaScript强制显示
                        if not slider_visible:
                            try:
                                slider_element.evaluate("""
                                    el => {
                                        el.style.visibility = 'visible';
                                        el.style.display = 'block';
                                        el.style.opacity = '1';
                                    }
                                """)
                                logger.info(f"【{user_id}】已尝试通过JS强制显示滑块")
                                time.sleep(0.5)
                                slider_visible = True
                            except:
                                pass
                    
                    # 尝试滚动到视图（缩短超时时间）
                    if slider_visible:
                        try:
                            slider_element.scroll_into_view_if_needed(timeout=3000)
                            time.sleep(random.uniform(0.15, 0.3))
                        except Exception as scroll_e:
                            logger.debug(f"【{user_id}】滚动到滑块失败: {scroll_e}，跳过此步骤")
                except Exception as prep_e:
                    logger.warning(f"【{user_id}】准备滑块失败: {prep_e}，继续尝试")
                
                # 使用复用的滑块拖动函数（来自 patch_simulate_slide）
                try:
                    # 调用复用的滑块拖动函数
                    drag_success = _execute_slider_drag(page, slider_element, distance, user_id)
                    
                    if not drag_success:
                        logger.warning(f"【{user_id}】滑块拖动失败")
                        continue
                    
                    # 等待验证结果
                    time.sleep(2)
                    
                    # 检查验证结果（优先检查失败消息，再检查成功标志）
                    verification_success = False
                    verification_failed = False
                    
                    # 方式1: 优先检查是否显示验证失败消息（最重要！）
                    try:
                        # 检查验证失败的消息（使用多种方式）
                        failure_selectors = [
                            'text:验证失败',
                            'text:点击框体重试',
                            'x://*[contains(text(), "验证失败")]',  # XPath
                            'x://*[contains(text(), "点击框体重试")]',  # XPath
                            '.nc-wrapper:contains("验证失败")',
                            'div:contains("验证失败")',
                            '[class*="error"]:contains("验证失败")',
                        ]
                        
                        # 也检查页面HTML中是否包含失败文本
                        page_html = ''
                        try:
                            page_html = page.content()
                        except:
                            pass
                        
                        for selector in failure_selectors:
                            try:
                                # Playwright 选择器处理
                                if selector.startswith('text:'):
                                    # text:验证失败 -> 使用文本选择器
                                    text_content = selector.replace('text:', '')
                                    failure_msg = page.locator(f'text={text_content}').first
                                    if failure_msg.count() > 0:
                                        logger.warning(f"【{user_id}】⚠️ 检测到验证失败提示: {selector}")
                                        verification_failed = True
                                        break
                                elif selector.startswith('x://'):
                                    # XPath 选择器
                                    xpath = selector.replace('x://', '')
                                    failure_msg = page.locator(f'xpath={xpath}').first
                                    if failure_msg.count() > 0:
                                        logger.warning(f"【{user_id}】⚠️ 检测到验证失败提示: {selector}")
                                        verification_failed = True
                                        break
                                else:
                                    failure_msg = page.query_selector(selector)
                                    if failure_msg:
                                        logger.warning(f"【{user_id}】⚠️ 检测到验证失败提示: {selector}")
                                        verification_failed = True
                                        break
                            except:
                                continue
                        
                        # 如果选择器没找到，检查页面HTML文本
                        if not verification_failed and page_html:
                            if '验证失败' in page_html or '点击框体重试' in page_html:
                                logger.warning(f"【{user_id}】⚠️ 在页面HTML中检测到验证失败文本")
                                verification_failed = True
                        
                        # 如果检测到失败，点击重试
                        if verification_failed:
                            # 查找并点击重试按钮/框体
                            retry_selectors = [
                                '.nc-wrapper',  # 滑块包装器
                                '#nc_1_wrapper',  # 滑块外层容器
                                'div[class*="nc-wrapper"]',  # 包含nc-wrapper的div
                                'text:点击框体重试',
                                'x://*[contains(text(), "点击框体重试")]',  # XPath
                                '[class*="error"]',  # 错误提示框
                            ]
                            
                            retry_clicked = False
                            for retry_selector in retry_selectors:
                                try:
                                    if retry_selector.startswith('text:'):
                                        text_content = retry_selector.replace('text:', '')
                                        retry_element = page.locator(f'text={text_content}').first
                                        if retry_element.count() > 0:
                                            logger.info(f"【{user_id}】找到重试元素: {retry_selector}，点击重试...")
                                            retry_element.click()
                                            time.sleep(1.5)
                                            retry_clicked = True
                                            break
                                    elif retry_selector.startswith('x://'):
                                        xpath = retry_selector.replace('x://', '')
                                        retry_element = page.locator(f'xpath={xpath}').first
                                        if retry_element.count() > 0:
                                            logger.info(f"【{user_id}】找到重试元素: {retry_selector}，点击重试...")
                                            retry_element.click()
                                            time.sleep(1.5)
                                            retry_clicked = True
                                            break
                                    else:
                                        retry_element = page.query_selector(retry_selector)
                                        if retry_element:
                                            logger.info(f"【{user_id}】找到重试元素: {retry_selector}，点击重试...")
                                            retry_element.click()
                                            time.sleep(1.5)
                                            retry_clicked = True
                                            break
                                except:
                                    continue
                            
                            if not retry_clicked:
                                # 如果找不到重试按钮，尝试点击滑块区域或错误区域
                                try:
                                    # 尝试点击包含错误消息的元素
                                    error_elem1 = page.locator('xpath=//*[contains(text(), "验证失败")]').first
                                    error_elem2 = page.query_selector('.nc-wrapper')
                                    
                                    if error_elem1.count() > 0:
                                        logger.info(f"【{user_id}】点击错误区域重试...")
                                        error_elem1.click()
                                        time.sleep(1.5)
                                        retry_clicked = True
                                    elif error_elem2:
                                        logger.info(f"【{user_id}】点击错误区域重试...")
                                        error_elem2.click()
                                        time.sleep(1.5)
                                        retry_clicked = True
                                except:
                                    pass
                            
                            if retry_clicked:
                                logger.info(f"【{user_id}】已点击重试，等待界面刷新...")
                                time.sleep(2)  # 等待重试界面加载
                    except Exception as check_e:
                        logger.debug(f"【{user_id}】检查验证失败消息时出错: {check_e}")
                    
                    # 方式2: 只有在没有失败消息的情况下，才检查是否成功
                    if not verification_failed:
                        try:
                            # 检查滑块元素是否消失（成功标志）
                            slider_check = None
                            try:
                                slider_check = page.query_selector('#nc_1_n1z')
                            except:
                                pass
                            
                            if not slider_check:
                                # 滑块元素不存在，再检查是否真的没有失败消息
                                # 再次确认没有失败消息
                                has_failure_text = False
                                try:
                                    page_html_check = page.content()
                                    if '验证失败' in page_html_check or '点击框体重试' in page_html_check:
                                        has_failure_text = True
                                except:
                                    pass
                                
                                if not has_failure_text:
                                    # 检查多个滑块相关元素是否都不存在
                                    slider_related_selectors = [
                                        '#nc_1_n1z',  # 滑块按钮
                                        '.nc_scale',  # 滑块轨道
                                        '.nc-wrapper',  # 滑块包装器
                                    ]
                                    
                                    all_missing = True
                                    for selector in slider_related_selectors:
                                        try:
                                            element = page.query_selector(selector)
                                            if element:
                                                all_missing = False
                                                break
                                        except:
                                            continue
                                    
                                    if all_missing:
                                        logger.info(f"【{user_id}】✅ 所有滑块元素都已消失且无失败消息，验证成功！")
                                        verification_success = True
                                    else:
                                        logger.debug(f"【{user_id}】滑块元素消失但仍有其他滑块元素存在")
                                else:
                                    logger.warning(f"【{user_id}】⚠️ 滑块元素消失但检测到失败消息，判定为失败")
                                    verification_failed = True
                            else:
                                logger.debug(f"【{user_id}】滑块元素仍存在，验证未完成")
                        except Exception as success_check_e:
                            logger.debug(f"【{user_id}】检查验证成功时出错: {success_check_e}")
                    
                    # 根据验证结果决定下一步
                    if verification_success:
                        logger.info(f"【{user_id}】✅ 滑块验证成功！")
                        return True
                    elif verification_failed:
                        logger.warning(f"【{user_id}】⚠️ 滑块验证失败，已点击重试，准备重新滑动...")
                        # 不返回，继续循环重试
                    else:
                        logger.warning(f"【{user_id}】⚠️ 滑块验证状态未知，准备重试...")
                        time.sleep(2)
                        
                except Exception as slide_e:
                    logger.error(f"【{user_id}】滑动操作失败: {slide_e}")
                    time.sleep(2)
                    # 不要continue，让attempt计数增加
                
            except Exception as e:
                logger.error(f"【{user_id}】滑块验证处理异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                time.sleep(2)
                # 不要continue，让attempt计数增加
        
        # 达到最大尝试次数，刷新页面重试
        logger.error(f"【{user_id}】❌ 滑块验证失败：已达到最大尝试次数({max_attempts})，准备刷新页面重试...")
        
        # 获取真正的 page 对象（兼容 Frame）
        # Frame 对象有 page 属性，指向其父 Page 对象
        if hasattr(page, 'page') and page.page is not None:
            real_page = page.page
        else:
            real_page = page
        
        logger.debug(f"【{user_id}】page 类型: {type(page).__name__}, real_page 类型: {type(real_page).__name__}")
        
        try:
            logger.info(f"【{user_id}】刷新浏览器页面（重新加载）...")
            logger.info(f"【{user_id}】当前URL: {real_page.url}")
            
            # 使用 reload() 方法刷新当前页面
            real_page.reload(wait_until='domcontentloaded', timeout=30000)
            time.sleep(3)  # 等待页面加载
            
            logger.info(f"【{user_id}】浏览器页面刷新完成")
            logger.info(f"【{user_id}】刷新后URL: {real_page.url}")
            logger.info(f"【{user_id}】等待滑块出现...")
            time.sleep(2)
            
            # 重新检测滑块
            slider_selectors = [
                '#nc_1_n1z',
                'span[id*="nc_1_n1z"]',
                'span.nc-lang-cnt[data-nc-lang="SLIDE"]',
                "xpath=//span[contains(text(), '向右滑动验证')]",
                'text=向右滑动验证',
                '.nc_scale',
                '.nc-wrapper',
                '.nc-iconfont',
            ]
            
            # 等待滑块出现（最多等待10秒）
            slider_appeared = False
            for wait_time in range(10):
                for selector in slider_selectors:
                    try:
                        slider_check = real_page.query_selector(selector)
                        if slider_check:
                            logger.info(f"【{user_id}】✓ 检测到滑块元素: {selector}")
                            slider_appeared = True
                            break
                    except:
                        continue
                
                if slider_appeared:
                    break
                
                time.sleep(1)
            
            if slider_appeared:
                logger.info(f"【{user_id}】滑块已出现，重新开始验证...")
                # 递归调用自身，重新验证（使用较少的尝试次数避免无限循环）
                return _handle_slider_verification(real_page, user_id, max_attempts=5)
            else:
                logger.warning(f"【{user_id}】⚠️ 刷新后未检测到滑块，检查是否已登录成功...")
                
                # 尝试检查登录是否成功（等待最多30秒）
                logger.info(f"【{user_id}】等待30秒检查登录状态...")
                login_success = False
                
                for check_attempt in range(30):
                    try:
                        # 调用登录成功检查函数
                        if _check_login_success_by_element(real_page, user_id):
                            logger.success(f"【{user_id}】✅ 检测到登录成功！")
                            login_success = True
                            break
                    except Exception as check_e:
                        logger.debug(f"【{user_id}】检查登录状态异常: {check_e}")
                    
                    time.sleep(1)
                    
                    # 每5秒输出一次进度
                    if (check_attempt + 1) % 5 == 0:
                        logger.info(f"【{user_id}】已等待 {check_attempt + 1}/30 秒...")
                
                if login_success:
                    logger.success(f"【{user_id}】✅ 登录验证成功！")
                    return True
                else:
                    logger.error(f"【{user_id}】❌ 等待30秒后仍未检测到登录成功")
                    return False
                
        except Exception as refresh_e:
            logger.error(f"【{user_id}】刷新页面失败: {refresh_e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        
    except Exception as e:
        logger.error(f"【{user_id}】滑块验证检测异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def patch_check_date_validity():
    """
    猴子补丁：替换 _check_date_validity 方法
    在导入 xianyu_slider_stealth 后调用此函数
    """
    try:
        # 尝试多种导入方式
        try:
            from utils.xianyu_slider_stealth import XianyuSliderStealth
        except ImportError:
            try:
                from xianyu_slider_stealth import XianyuSliderStealth
            except ImportError:
                import sys
                import os
                # 添加utils目录到路径
                utils_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
                if utils_dir not in sys.path:
                    sys.path.insert(0, utils_dir)
                from xianyu_slider_stealth import XianyuSliderStealth
        
        # 保存原始方法（如果需要）
        original_method = XianyuSliderStealth._check_date_validity
        
        def new_check_date_validity(self) -> bool:
            """
            新的日期有效性检查方法
            支持通过环境变量 SLIDER_VALIDITY_DAYS 自定义剩余使用天数
            """
            try:
                # 从环境变量读取最大使用天数，默认30天
                import os
                max_days = int(os.getenv('SLIDER_VALIDITY_DAYS', '30'))
                
                # 获取轨迹历史目录
                trajectory_history_dir = os.path.join(os.getcwd(), 'trajectory_history')
                history_file = os.path.join(trajectory_history_dir, f"{self.user_id}_history.json")
                
                if not os.path.exists(history_file):
                    # 文件不存在，认为有效
                    logger.debug(f"【{self.user_id}】历史记录文件不存在，日期验证通过")
                    return True
                
                # 检查文件修改时间
                file_mtime = os.path.getmtime(history_file)
                file_date = datetime.fromtimestamp(file_mtime)
                now = datetime.now()
                days_diff = (now - file_date).days
                remaining_days = max_days - days_diff
                
                # 如果文件超过最大天数未更新，认为过期
                if days_diff > max_days:
                    logger.warning(f"【{self.user_id}】日期验证失败，已超过最大使用天数 {max_days} 天（实际: {days_diff} 天）")
                    return False
                
                # 检查文件内容中的时间戳
                import json
                try:
                    with open(history_file, 'r', encoding='utf-8') as f:
                        history = json.load(f)
                    
                    if history:
                        # 获取最新的记录时间戳
                        latest_timestamp = max(h.get('timestamp', 0) for h in history if isinstance(h, dict))
                        if latest_timestamp > 0:
                            latest_date = datetime.fromtimestamp(latest_timestamp)
                            days_diff = (now - latest_date).days
                            remaining_days = max_days - days_diff
                            
                            if days_diff > max_days:
                                logger.warning(f"【{self.user_id}】日期验证失败，学习数据记录已超过最大使用天数 {max_days} 天（实际: {days_diff} 天）")
                                return False
                            
                            logger.info(f"【{self.user_id}】日期验证通过，剩余可用天数: {remaining_days} 天")
                            return True
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.warning(f"【{self.user_id}】读取历史记录文件失败: {e}，使用文件修改时间判断")
                
                # 使用文件修改时间判断
                logger.info(f"【{self.user_id}】日期验证通过，剩余可用天数: {remaining_days} 天")
                return True
                
            except Exception as e:
                logger.error(f"【{self.user_id}】日期有效性检查异常: {e}")
                import traceback
                traceback.print_exc()
                # 异常时返回 True，允许继续使用
                return True
        
        # 替换方法
        XianyuSliderStealth._check_date_validity = new_check_date_validity
        
        logger.info("✓ _check_date_validity 方法已通过猴子补丁替换")
        return True
        
    except ImportError as e:
        logger.error(f"无法导入 xianyu_slider_stealth 模块: {e}")
        return False
    except Exception as e:
        logger.error(f"应用补丁失败: {e}")
        import traceback
        traceback.print_exc()
        return False




def _execute_slider_drag(page, slider_element, distance, user_id="unknown"):
    """
    执行滑块拖动的核心逻辑（可复用）
    
    Args:
        page: Playwright Page对象或Frame对象
        slider_element: 滑块元素
        distance: 滑动距离
        user_id: 用户ID
    
    Returns:
        bool: 是否成功
    """
    import random
    import time
    
    try:
        logger.info(f"【{user_id}】开始执行滑块拖动，距离={distance:.1f}px")
        
        # 兼容 Frame 或 Page：Frame 没有 mouse 属性，需要获取其 page 对象
        # Frame 对象有 page 属性，指向其父 Page 对象
        if hasattr(page, 'page') and page.page is not None:
            mouse_page = page.page
        else:
            mouse_page = page
        
        logger.debug(f"【{user_id}】page 类型: {type(page).__name__}, mouse_page 类型: {type(mouse_page).__name__}")
        
        # 生成优化的轨迹
        def generate_optimized_trajectory(distance: float) -> list:
            """
            生成优化的人类滑动轨迹（基于高成功率JS代码逻辑）
            :param distance: 目标滑动距离
            :return: 轨迹点列表，每个点包含 {'dx': x移动, 'dy': y移动, 'pause': 可选停顿时间}
            """
            trajectory = []
            covered_distance = 0.0
            
            # 第一阶段：加速阶段（前30%）
            accel_steps = random.randint(12, 18)
            for i in range(accel_steps):
                progress = (i + 1) / accel_steps
                # 速度从2到10像素逐步增加
                speed = 2 + progress * 8
                dx = speed
                # Y轴微小抖动
                dy = random.uniform(-1.0, 1.0)
                
                trajectory.append({'dx': dx, 'dy': dy})
                covered_distance += dx
                
                # 如果已经超过30%，提前结束加速阶段
                if covered_distance >= distance * 0.3:
                    break
            
            # 第二阶段：匀速阶段（中间40%，直到70%）
            while covered_distance < distance * 0.7:
                dx = random.uniform(8.0, 12.0)
                dy = random.uniform(-1.5, 1.5)
                
                # 随机犹豫（10%概率）
                pause = 0
                if random.random() < 0.1:
                    pause = random.randint(30, 80)  # 毫秒
                
                trajectory.append({'dx': dx, 'dy': dy, 'pause': pause})
                covered_distance += dx
                
                # 防止超出太多
                if covered_distance >= distance * 0.75:
                    break
            
            # 第三阶段：减速阶段（最后30%）
            remaining_distance = distance - covered_distance
            decel_steps = random.randint(18, 25)
            
            for i in range(decel_steps):
                progress = (i + 1) / decel_steps
                # 速度逐渐减小
                speed = (remaining_distance / decel_steps) * (1 - progress * 0.5)
                dx = max(speed, 0.5)  # 最小0.5像素
                dy = random.uniform(-0.8, 0.8)
                
                trajectory.append({'dx': dx, 'dy': dy})
                covered_distance += dx
                
                if covered_distance >= distance:
                    break
            
            # 第四阶段：超调回退（模拟人类修正行为）
            if covered_distance < distance:
                # 如果还没到目标，继续前进一点
                final_push = distance - covered_distance
                trajectory.append({'dx': final_push, 'dy': random.uniform(-0.5, 0.5)})
                covered_distance = distance
            
            # 超调：超出一点再回退（模拟人的修正行为）
            overshoot = random.randint(5, 15)
            trajectory.append({'dx': overshoot, 'dy': random.uniform(-0.5, 0.5)})
            trajectory.append({'dx': -overshoot * 0.5, 'dy': 0})
            
            return trajectory
        
        # 获取滑块位置
        try:
            box = slider_element.bounding_box()
            if not box:
                logger.error(f"【{user_id}】无法获取滑块按钮位置")
                return False
            
            slider_x = box['x'] + box['width'] / 2
            slider_y = box['y'] + box['height'] / 2
            logger.debug(f"【{user_id}】滑块位置: ({slider_x}, {slider_y})")
        except Exception as e:
            logger.error(f"【{user_id}】获取滑块位置失败: {e}")
            return False
        
        # 第一阶段：移动到滑块附近（模拟人类寻找滑块）
        try:
            # 先移动到滑块附近（稍微偏左）
            offset_x = random.uniform(-30, -10)
            offset_y = random.uniform(-15, 15)
            mouse_page.mouse.move(
                slider_x + offset_x,
                slider_y + offset_y,
                steps=random.randint(5, 10)
            )
            time.sleep(random.uniform(0.15, 0.3))
            
            # 再精确移动到滑块中心
            mouse_page.mouse.move(
                slider_x,
                slider_y,
                steps=random.randint(3, 6)
            )
            time.sleep(random.uniform(0.1, 0.25))
        except Exception as e:
            logger.warning(f"【{user_id}】移动到滑块失败: {e}，继续尝试")
        
        # 第二阶段：悬停在滑块上
        try:
            slider_element.hover(timeout=2000)
            time.sleep(random.uniform(0.1, 0.3))
        except Exception as e:
            logger.warning(f"【{user_id}】悬停滑块失败: {e}")
        
        # 第三阶段：按下鼠标
        try:
            mouse_page.mouse.move(slider_x, slider_y)
            time.sleep(random.uniform(0.05, 0.15))
            mouse_page.mouse.down()
            time.sleep(random.uniform(0.05, 0.15))
        except Exception as e:
            logger.error(f"【{user_id}】按下鼠标失败: {e}")
            return False
        
        # 第四阶段：执行滑动轨迹
        try:
            # 生成优化的轨迹
            optimized_trajectory = generate_optimized_trajectory(distance)
            logger.info(f"【{user_id}】生成优化轨迹: 距离={distance:.1f}px, 点数={len(optimized_trajectory)}")
            
            # 执行滑动
            start_time = time.time()
            current_x = slider_x
            current_y = slider_y
            
            # 执行拖动轨迹
            for i, point in enumerate(optimized_trajectory):
                dx = point.get('dx', 0)
                dy = point.get('dy', 0)
                pause = point.get('pause', 0)
                
                # 更新当前位置
                current_x += dx
                current_y += dy
                
                # 移动鼠标
                mouse_page.mouse.move(
                    current_x,
                    current_y,
                    steps=random.randint(1, 3)
                )
                
                # 延迟（根据是否有停顿）
                if pause > 0:
                    # 有停顿，使用停顿时间
                    time.sleep(pause / 1000.0)
                else:
                    # 正常延迟（1-3毫秒）
                    time.sleep(random.uniform(0.001, 0.003))
            
            # 释放鼠标
            time.sleep(random.uniform(0.02, 0.05))
            mouse_page.mouse.up()
            time.sleep(random.uniform(0.01, 0.03))
            
            # 触发click事件
            try:
                slider_element.evaluate(f"""
                    (slider) => {{
                        const event = new MouseEvent('click', {{
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: {current_x},
                            clientY: {current_y},
                            button: 0
                        }});
                        slider.dispatchEvent(event);
                    }}
                """)
            except Exception as e:
                logger.debug(f"【{user_id}】触发click事件失败（可忽略）: {e}")
            
            elapsed_time = time.time() - start_time
            logger.info(f"【{user_id}】滑动完成: 耗时={elapsed_time:.2f}秒, 最终位置=({current_x:.1f}, {current_y:.1f})")
            
            return True
            
        except Exception as e:
            logger.error(f"【{user_id}】执行滑动轨迹失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 确保释放鼠标
            try:
                mouse_page.mouse.up()
            except:
                pass
            return False
            
    except Exception as e:
        logger.error(f"【{user_id}】滑块拖动异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def patch_simulate_slide():
    """
    猴子补丁：替换 simulate_slide 方法以提高成功率
    在导入 xianyu_slider_stealth 后调用此函数
    """
    try:
        # 尝试多种导入方式
        try:
            from utils.xianyu_slider_stealth import XianyuSliderStealth
        except ImportError:
            try:
                from xianyu_slider_stealth import XianyuSliderStealth
            except ImportError:
                import sys
                import os
                utils_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
                if utils_dir not in sys.path:
                    sys.path.insert(0, utils_dir)
                from xianyu_slider_stealth import XianyuSliderStealth
        import random
        import time
        from playwright.sync_api import ElementHandle
        
        # 保存原始方法（如果需要）
        original_method = XianyuSliderStealth.simulate_slide
        
        def generate_optimized_trajectory(distance: float) -> list:
            """
            生成优化的人类滑动轨迹（基于高成功率JS代码逻辑）
            :param distance: 目标滑动距离
            :return: 轨迹点列表，每个点包含 {'dx': x移动, 'dy': y移动, 'pause': 可选停顿时间}
            """
            trajectory = []
            covered_distance = 0.0
            
            # 第一阶段：加速阶段（前30%）
            accel_steps = random.randint(12, 18)
            for i in range(accel_steps):
                progress = (i + 1) / accel_steps
                # 速度从2到10像素逐步增加
                speed = 2 + progress * 8
                dx = speed
                # Y轴微小抖动
                dy = random.uniform(-1.0, 1.0)
                
                trajectory.append({'dx': dx, 'dy': dy})
                covered_distance += dx
                
                # 如果已经超过30%，提前结束加速阶段
                if covered_distance >= distance * 0.3:
                    break
            
            # 第二阶段：匀速阶段（中间40%，直到70%）
            while covered_distance < distance * 0.7:
                dx = random.uniform(8.0, 12.0)
                dy = random.uniform(-1.5, 1.5)
                
                # 随机犹豫（10%概率）
                pause = 0
                if random.random() < 0.1:
                    pause = random.randint(30, 80)  # 毫秒
                
                trajectory.append({'dx': dx, 'dy': dy, 'pause': pause})
                covered_distance += dx
                
                # 防止超出太多
                if covered_distance >= distance * 0.75:
                    break
            
            # 第三阶段：减速阶段（最后30%）
            remaining_distance = distance - covered_distance
            decel_steps = random.randint(18, 25)
            
            for i in range(decel_steps):
                progress = (i + 1) / decel_steps
                # 速度逐渐减小
                speed = (remaining_distance / decel_steps) * (1 - progress * 0.5)
                dx = max(speed, 0.5)  # 最小0.5像素
                dy = random.uniform(-0.8, 0.8)
                
                trajectory.append({'dx': dx, 'dy': dy})
                covered_distance += dx
                
                if covered_distance >= distance:
                    break
            
            # 第四阶段：超调回退（模拟人类修正行为）
            if covered_distance < distance:
                # 如果还没到目标，继续前进一点
                final_push = distance - covered_distance
                trajectory.append({'dx': final_push, 'dy': random.uniform(-0.5, 0.5)})
                covered_distance = distance
            
            # 超调：超出一点再回退（模拟人的修正行为）
            overshoot = random.randint(5, 15)
            trajectory.append({'dx': overshoot, 'dy': random.uniform(-0.5, 0.5)})
            trajectory.append({'dx': -overshoot * 0.5, 'dy': 0})
            
            return trajectory
        
        def patched_simulate_slide(self, slider_button: ElementHandle, trajectory: Any) -> Any:
            """
            优化的滑动模拟方法
            实现更人性化的滑动轨迹，提高成功率
            """
            user_id = getattr(self, 'user_id', 'unknown')
            try:
                logger.info(f"【{user_id}】开始优化滑动模拟...")
                
                # 等待页面稳定
                time.sleep(random.uniform(0.1, 0.3))
                
                # 获取滑块按钮的位置信息
                try:
                    box = slider_button.bounding_box()
                    if not box:
                        logger.error(f"【{user_id}】无法获取滑块按钮位置")
                        return False
                    
                    slider_x = box['x'] + box['width'] / 2
                    slider_y = box['y'] + box['height'] / 2
                    logger.debug(f"【{user_id}】滑块位置: ({slider_x}, {slider_y})")
                except Exception as e:
                    logger.error(f"【{user_id}】获取滑块位置失败: {e}")
                    return False
                
                # 第一阶段：移动到滑块附近（模拟人类寻找滑块）
                try:
                    # 先移动到滑块附近（稍微偏左）
                    offset_x = random.uniform(-30, -10)
                    offset_y = random.uniform(-15, 15)
                    self.page.mouse.move(
                        slider_x + offset_x,
                        slider_y + offset_y,
                        steps=random.randint(5, 10)
                    )
                    time.sleep(random.uniform(0.15, 0.3))
                    
                    # 再精确移动到滑块中心
                    self.page.mouse.move(
                        slider_x,
                        slider_y,
                        steps=random.randint(3, 6)
                    )
                    time.sleep(random.uniform(0.1, 0.25))
                except Exception as e:
                    logger.warning(f"【{user_id}】移动到滑块失败: {e}，继续尝试")
                
                # 第二阶段：悬停在滑块上
                try:
                    slider_button.hover(timeout=2000)
                    time.sleep(random.uniform(0.1, 0.3))
                except Exception as e:
                    logger.warning(f"【{user_id}】悬停滑块失败: {e}")
                
                # 第三阶段：按下鼠标
                try:
                    self.page.mouse.move(slider_x, slider_y)
                    time.sleep(random.uniform(0.05, 0.15))
                    self.page.mouse.down()
                    time.sleep(random.uniform(0.05, 0.15))
                except Exception as e:
                    logger.error(f"【{user_id}】按下鼠标失败: {e}")
                    return False
                
                # 第四阶段：执行滑动轨迹
                try:
                    # 计算滑动距离
                    total_distance = 0
                    
                    # 方法1: 从传入的轨迹参数中提取距离
                    if isinstance(trajectory, list) and len(trajectory) > 0:
                        first_item = trajectory[0]
                        
                        if isinstance(first_item, dict):
                            # 轨迹是字典列表，提取最大x值作为距离
                            max_x = max((t.get('x', 0) for t in trajectory if isinstance(t, dict)), default=0)
                            if max_x > 0:
                                total_distance = max_x
                            else:
                                # 如果字典中没有x，尝试从最后一个点的x值计算
                                last_point = trajectory[-1] if trajectory else {}
                                total_distance = last_point.get('x', 0) if isinstance(last_point, dict) else 0
                        
                        elif isinstance(first_item, (tuple, list)) and len(first_item) >= 1:
                            # 轨迹是 tuple 或列表的列表，如 [(x, y), ...] 或 [[x, y], ...]
                            try:
                                # 提取所有 x 值（第一个元素）
                                x_values = [item[0] if isinstance(item, (tuple, list)) and len(item) > 0 else 0 
                                           for item in trajectory if isinstance(item, (tuple, list))]
                                if x_values:
                                    total_distance = float(max(x_values))
                                else:
                                    total_distance = 0
                            except (IndexError, ValueError, TypeError) as e:
                                logger.warning(f"【{user_id}】解析 tuple/list 格式轨迹失败: {e}")
                                total_distance = 0
                        
                        elif isinstance(first_item, (int, float)):
                            # 轨迹是数字列表（绝对位置）
                            try:
                                last_value = trajectory[-1]
                                if isinstance(last_value, (int, float)):
                                    total_distance = float(last_value)
                                else:
                                    total_distance = 0
                            except (ValueError, TypeError) as e:
                                logger.warning(f"【{user_id}】解析数字格式轨迹失败: {e}")
                                total_distance = 0
                        
                        else:
                            # 未知格式，尝试转换
                            try:
                                last_value = trajectory[-1]
                                if isinstance(last_value, (int, float)):
                                    total_distance = float(last_value)
                                elif isinstance(last_value, (tuple, list)) and len(last_value) > 0:
                                    total_distance = float(last_value[0])
                                else:
                                    total_distance = 0
                            except (ValueError, TypeError, IndexError) as e:
                                logger.warning(f"【{user_id}】无法解析轨迹格式: {type(first_item)}, 错误: {e}")
                                total_distance = 0
                    else:
                        total_distance = 0
                    
                    # 方法2: 如果无法从轨迹获取，从滑块轨道计算
                    if total_distance <= 0:
                        try:
                            # 尝试使用 calculate_slide_distance 方法（如果存在）
                            if hasattr(self, 'calculate_slide_distance'):
                                slider_track = self.page.query_selector('.nc_scale')
                                if slider_track:
                                    total_distance = self.calculate_slide_distance(slider_button, slider_track)
                            
                            # 如果还是无法获取，直接计算
                            if total_distance <= 0:
                                slider_track = self.page.query_selector('.nc_scale')
                                if slider_track:
                                    track_box = slider_track.bounding_box()
                                    button_box = slider_button.bounding_box()
                                    if track_box and button_box:
                                        total_distance = track_box['width'] - button_box['width']
                                        logger.debug(f"【{user_id}】从轨道计算距离: {total_distance}px")
                        except Exception as e:
                            logger.warning(f"【{user_id}】计算滑动距离失败: {e}")
                    
                    # 方法3: 使用默认距离
                    if total_distance <= 0:
                        total_distance = 300  # 默认距离
                        logger.warning(f"【{user_id}】使用默认滑动距离: {total_distance}px")
                    
                    logger.info(f"【{user_id}】滑动距离: {total_distance:.1f}px")
                    
                    # 生成优化的轨迹（基于高成功率JS代码逻辑）
                    optimized_trajectory = generate_optimized_trajectory(total_distance)
                    logger.info(f"【{user_id}】生成优化轨迹: 距离={total_distance:.1f}px, 点数={len(optimized_trajectory)}")
                    
                    # 执行滑动（模拟JS代码的执行方式）
                    start_time = time.time()
                    current_x = slider_x
                    current_y = slider_y
                    
                    # 按下鼠标（模拟mousedown）
                    self.page.mouse.move(current_x, current_y)
                    time.sleep(random.uniform(0.05, 0.1))
                    self.page.mouse.down()
                    time.sleep(random.uniform(0.05, 0.1))
                    
                    # 执行拖动轨迹
                    for i, point in enumerate(optimized_trajectory):
                        dx = point.get('dx', 0)
                        dy = point.get('dy', 0)
                        pause = point.get('pause', 0)
                        
                        # 更新当前位置
                        current_x += dx
                        current_y += dy
                        
                        # 移动鼠标
                        self.page.mouse.move(
                            current_x,
                            current_y,
                            steps=random.randint(1, 3)
                        )
                        
                        # 延迟（根据是否有停顿）
                        if pause > 0:
                            # 有停顿，使用停顿时间
                            time.sleep(pause / 1000.0)
                        else:
                            # 正常延迟（1-3毫秒，模拟JS代码）
                            time.sleep(random.uniform(0.001, 0.003))
                    
                    # 释放鼠标（模拟mouseup和click）
                    time.sleep(random.uniform(0.02, 0.05))
                    self.page.mouse.up()
                    time.sleep(random.uniform(0.01, 0.03))
                    
                    # 触发click事件（通过JavaScript，更接近真实行为）
                    try:
                        self.page.evaluate(f"""
                            (function() {{
                                const slider = arguments[0];
                                const event = new MouseEvent('click', {{
                                    bubbles: true,
                                    cancelable: true,
                                    view: window,
                                    clientX: {current_x},
                                    clientY: {current_y},
                                    button: 0
                                }});
                                slider.dispatchEvent(event);
                            }})(arguments[0]);
                        """, slider_button)
                    except Exception as e:
                        logger.debug(f"【{user_id}】触发click事件失败（可忽略）: {e}")
                    
                    elapsed_time = time.time() - start_time
                    logger.info(f"【{user_id}】滑动完成: 耗时={elapsed_time:.2f}秒, 最终位置=({current_x:.1f}, {current_y:.1f})")
                    
                except Exception as e:
                    logger.error(f"【{user_id}】执行滑动轨迹失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    # 确保释放鼠标
                    try:
                        self.page.mouse.up()
                    except:
                        pass
                    return False
                
                return True
                
            except Exception as e:
                logger.error(f"【{user_id}】滑动模拟异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False
        
        # 替换方法
        XianyuSliderStealth.simulate_slide = patched_simulate_slide
        
        logger.info("✓ simulate_slide 方法已通过猴子补丁替换（优化滑动轨迹）")
        return True
        
    except ImportError as e:
        logger.error(f"无法导入 xianyu_slider_stealth 模块: {e}")
        return False
    except Exception as e:
        logger.error(f"应用 simulate_slide 补丁失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def _find_frame_with_login(page, selectors, user_id):
    """查找包含登录元素的frame"""
    try:
        logger.info(f"【{user_id}】开始查找包含登录元素的frame...")
        logger.info(f"【{user_id}】页面中共有 {len(page.frames)} 个frame")
        
        # 首先尝试在主页面中查找
        for selector in selectors:
            try:
                element = page.query_selector(selector)
                if element:
                    logger.info(f"【{user_id}】✓ 在主页面找到登录元素: {selector}")
                    return page
            except:
                continue
        
        # 如果主页面没有，尝试在所有frame中查找
        for idx, frame in enumerate(page.frames):
            try:
                frame_url = frame.url
                logger.debug(f"【{user_id}】检查Frame {idx}: {frame_url}")
                
                for selector in selectors:
                    try:
                        element = frame.query_selector(selector)
                        if element:
                            logger.info(f"【{user_id}】✓ 在Frame {idx} 找到登录元素: {selector}")
                            logger.info(f"【{user_id}】Frame URL: {frame_url}")
                            return frame
                    except:
                        continue
            except Exception as e:
                logger.debug(f"【{user_id}】检查Frame {idx} 失败: {e}")
                continue
    except Exception as e:
        logger.error(f"【{user_id}】查找frame失败: {e}")
    
    logger.warning(f"【{user_id}】未找到包含登录元素的frame")
    return None


def _detect_slider_verification_in_page(page_or_frame, user_id):
    """检测是否存在滑块验证（检测当前frame和所有子frame）
    
    Returns:
        tuple: (has_slider, slider_frame) - 是否有滑块，滑块所在的frame
    """
    logger.debug(f"【{user_id}】检测滑块验证...")
    
    slider_selectors = [
        '#nc_1_n1z',  # 滑块按钮
        'span[id*="nc_1_n1z"]',  # 滑块按钮变体
        '.nc_scale',  # 滑块轨道
        '.nc-wrapper',  # 滑块包装器
        '.nc-iconfont',  # 滑块图标
        'span.nc-lang-cnt',  # 滑块文本
        'span.nc-lang-cnt[data-nc-lang="SLIDE"]',  # 滑块文本（带属性）
        "xpath=//span[contains(text(), '请按住滑块')]",
        "xpath=//span[contains(text(), '向右滑动验证')]",
    ]
    
    # 首先在当前frame中检测
    for selector in slider_selectors:
        try:
            element = page_or_frame.query_selector(selector)
            if element and element.is_visible():
                logger.info(f"【{user_id}】✅ 检测到滑块验证: {selector}")
                return True, page_or_frame
        except Exception as e:
            logger.debug(f"【{user_id}】检测选择器 {selector} 失败: {e}")
            continue
    
    # 如果当前frame没有，尝试在所有frame中查找（如果传入的是page对象）
    try:
        if hasattr(page_or_frame, 'frames'):
            for idx, frame in enumerate(page_or_frame.frames):
                try:
                    for selector in slider_selectors:
                        try:
                            element = frame.query_selector(selector)
                            if element and element.is_visible():
                                logger.info(f"【{user_id}】✅ 在Frame {idx} 检测到滑块验证: {selector}")
                                return True, frame
                        except:
                            continue
                except Exception as e:
                    logger.debug(f"【{user_id}】检查Frame {idx} 失败: {e}")
                    continue
    except:
        pass
    
    logger.debug(f"【{user_id}】未检测到滑块验证")
    return False, None


def _detect_qr_code_verification(page, user_id):
    """检测是否存在二维码/人脸验证"""
    logger.info(f"【{user_id}】检测二维码/人脸验证...")
    
    # 检测所有frames
    for idx, frame in enumerate(page.frames):
        try:
            frame_url = frame.url
            logger.debug(f"【{user_id}】检查Frame {idx} 是否有二维码: {frame_url}")
            
            # 二维码验证的选择器
            qr_selectors = [
                'img[alt*="二维码"]',
                'img[src*="qrcode"]',
                'div[class*="qrcode"]',
                'canvas[class*="qrcode"]',
                '.qr-code',
                '#qr-code',
            ]
            
            for selector in qr_selectors:
                try:
                    element = frame.query_selector(selector)
                    if element and element.is_visible():
                        logger.info(f"【{user_id}】✅ 在Frame {idx} 检测到二维码验证: {selector}")
                        logger.info(f"【{user_id}】二维码Frame URL: {frame_url}")
                        return True, frame
                except:
                    continue
            
            # 人脸验证的关键词
            face_keywords = ['拍摄脸部', '人脸验证', '人脸识别', '面部验证', '扫码验证']
            try:
                frame_content = frame.content()
                for keyword in face_keywords:
                    if keyword in frame_content:
                        logger.info(f"【{user_id}】✅ 在Frame {idx} 检测到人脸验证: {keyword}")
                        logger.info(f"【{user_id}】人脸验证Frame URL: {frame_url}")
                        return True, frame
            except:
                pass
                
        except Exception as e:
            logger.debug(f"【{user_id}】检查Frame {idx} 失败: {e}")
            continue
    
    logger.info(f"【{user_id}】未检测到二维码/人脸验证")
    return False, None


def _check_login_error(page, user_id: str) -> tuple:
    """
    检测登录是否出现错误（如账密错误）
    
    Args:
        page: Page对象
        user_id: 用户ID
    
    Returns:
        tuple: (has_error, error_message) - 是否有错误，错误消息
    """
    try:
        logger.debug(f"【{user_id}】检查登录错误...")
        
        # 检测账密错误
        error_selectors = [
            '.login-error-msg',  # 主要的错误消息类
            '[class*="error-msg"]',  # 包含error-msg的类
            'div:has-text("账密错误")',  # 包含"账密错误"文本的div
            'text=账密错误',  # 直接文本匹配
        ]
        
        # 在主页面和所有frame中查找
        frames_to_check = [page] + page.frames
        
        for frame in frames_to_check:
            try:
                for selector in error_selectors:
                    try:
                        element = frame.query_selector(selector)
                        if element and element.is_visible():
                            error_text = element.inner_text()
                            logger.error(f"【{user_id}】❌ 检测到登录错误: {error_text}")
                            return True, error_text
                    except:
                        continue
                        
                # 也检查页面HTML中是否包含错误文本
                try:
                    content = frame.content()
                    if '账密错误' in content or '账号密码错误' in content or '用户名或密码错误' in content:
                        logger.error(f"【{user_id}】❌ 页面内容中检测到账密错误")
                        return True, "账密错误"
                except:
                    pass
                    
            except:
                continue
        
        return False, None
        
    except Exception as e:
        logger.debug(f"【{user_id}】检查登录错误时出错: {e}")
        return False, None


def _check_login_success_by_element(page_or_context, user_id: str) -> bool:
    """
    通过检测页面元素来判断登录是否成功
    检查 rc-virtual-list-holder-inner 元素是否有数据
    
    Args:
        page_or_context: Page对象或Context对象
        user_id: 用户ID
    
    Returns:
        bool: 登录成功返回True，否则返回False
    """
    try:
        # 如果传入的是context，获取第一个page
        if hasattr(page_or_context, 'pages'):
            pages = page_or_context.pages
            if not pages:
                logger.debug(f"【{user_id}】没有可用的页面")
                return False
            page = pages[0]
        else:
            page = page_or_context
        
        # 检查目标元素
        selector = '.rc-virtual-list-holder-inner'
        logger.info(f"【{user_id}】========== 检查登录状态（通过页面元素） ==========")
        logger.info(f"【{user_id}】检查选择器: {selector}")
        
        # 查找元素
        element = page.query_selector(selector)
        
        if element:
            # 获取元素的子元素数量
            child_count = element.evaluate('el => el.children.length')
            inner_html = element.inner_html()
            inner_text = element.inner_text() if element.is_visible() else ""
            
            logger.info(f"【{user_id}】找到目标元素:")
            logger.info(f"【{user_id}】  - 子元素数量: {child_count}")
            logger.info(f"【{user_id}】  - 是否可见: {element.is_visible()}")
            logger.info(f"【{user_id}】  - innerText长度: {len(inner_text)}")
            logger.info(f"【{user_id}】  - innerHTML长度: {len(inner_html)}")
            logger.info(f"【{user_id}】  - innerHTML内容 (前200字符): {inner_html[:200]}")
            
            # 判断是否有数据：子元素数量大于0
            if child_count > 0:
                logger.success(f"【{user_id}】✅ 登录成功！检测到列表有 {child_count} 个子元素")
                logger.info(f"【{user_id}】================================================")
                return True
            else:
                logger.debug(f"【{user_id}】列表为空，登录未完成")
                logger.info(f"【{user_id}】================================================")
                return False
        else:
            logger.debug(f"【{user_id}】未找到目标元素: {selector}")
            logger.info(f"【{user_id}】================================================")
            return False
            
    except Exception as e:
        logger.debug(f"【{user_id}】检查登录状态时出错: {e}")
        import traceback
        logger.debug(f"【{user_id}】错误堆栈: {traceback.format_exc()}")
        return False


def _send_qr_verification_notification(cookie_id: str, qr_url: str):
    """发送二维码/人脸验证通知"""
    try:
        logger.info(f"【{cookie_id}】准备发送二维码/人脸验证通知...")
        
        # 构造通知消息
        notification_title = "闲鱼账号需要验证"
        notification_message = (
            f"⚠️ Token失效 - 需要人脸验证\n\n"
            f"账号ID: {cookie_id}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"密码登录需要人脸验证，请执行以下步骤：\n"
            f"1. 打开下方二维码链接\n"
            f"2. 使用闲鱼APP扫码\n"
            f"3. 完成人脸验证\n\n"
            f"验证链接:\n{qr_url}\n\n"
            f"请尽快完成验证以恢复账号正常使用。"
        )
        
        # 使用公共通知方法发送
        send_notification(cookie_id, notification_title, notification_message, "warning")
        
    except Exception as e:
        logger.error(f"【{cookie_id}】发送二维码验证通知失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


def patch_login_with_password_headful():
    """
    猴子补丁：重写 login_with_password_headful 方法
    使用 Playwright 实现更稳定的密码登录流程
    """
    try:
        # 尝试多种导入方式
        try:
            from utils.xianyu_slider_stealth import XianyuSliderStealth
        except ImportError:
            try:
                from xianyu_slider_stealth import XianyuSliderStealth
            except ImportError:
                import sys
                import os
                utils_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
                if utils_dir not in sys.path:
                    sys.path.insert(0, utils_dir)
                from xianyu_slider_stealth import XianyuSliderStealth
        import time
        import random
        
        # 保存原始方法（如果需要回退）
        original_method = getattr(XianyuSliderStealth, 'login_with_password_headful', None)
        
        def patched_login_with_password_headful(self, account: str, password: str, show_browser: bool) -> dict:
            """
            重写的密码登录方法
            使用 Playwright 实现更稳定的登录流程（整合test_slider_login.py的完整逻辑）
            
            Args:
                account: 账号
                password: 密码
                show_browser: 是否显示浏览器
            
            Returns:
                dict: Cookie字典，失败返回None或空字典
            """
            user_id = getattr(self, 'user_id', account)
            
            logger.info("开始密码登录流程...")
            logger.info(f"账号: {account}")
            logger.info(f"模式: {'有头模式' if show_browser else '无头模式'}")
            logger.info("=" * 60)
            
            try:
                # 导入 Playwright
                try:
                    from playwright.sync_api import sync_playwright
                    logger.info(f"【{user_id}】Playwright导入成功")
                except ImportError as e:
                    logger.error(f"【{user_id}】无法导入 Playwright: {e}")
                    if original_method:
                        logger.info(f"【{user_id}】回退到原始方法")
                        return original_method(self, account, password, show_browser)
                    return None
                
                # 启动浏览器
                mode_text = "有头模式" if show_browser else "无头模式"
                logger.info(f"【{user_id}】启动Playwright浏览器（{mode_text}）...")
                playwright = sync_playwright().start()
                
                try:
                    # 设置用户数据目录（保留缓存）
                    import os
                    user_data_dir = os.path.join(os.getcwd(), 'browser_data', f'user_{user_id}')
                    os.makedirs(user_data_dir, exist_ok=True)
                    logger.info(f"【{user_id}】使用用户数据目录: {user_data_dir}")
                    
                    # 设置浏览器启动参数（保留缓存）
                    browser_args = [
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-features=VizDisplayCompositor',
                    ]
                    
                    # 启动浏览器（使用持久化上下文）
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir,  # 第一个参数就是用户数据目录
                        headless=not show_browser,
                        args=browser_args,
                        viewport={'width': 1980, 'height': 1024},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
                        accept_downloads=True,
                        ignore_https_errors=False
                    )
                    
                    browser = context.browser
                    
                    # 创建页面
                    page = context.new_page()
                    logger.info(f"【{user_id}】浏览器已成功启动（{'有头' if show_browser else '无头'}模式）")
                    
                    try:
                        # 访问登录页面
                        login_url = "https://www.goofish.com/im"
                        logger.info(f"【{user_id}】访问登录页面: {login_url}")
                        page.goto(login_url, wait_until='networkidle', timeout=60000)
                        
                        # 等待页面加载
                        wait_time = 10 if not show_browser else 5
                        logger.info(f"【{user_id}】等待页面加载（{wait_time}秒）...")
                        time.sleep(wait_time)
                        
                        # 页面诊断信息
                        logger.info(f"【{user_id}】========== 页面诊断信息 ==========")
                        logger.info(f"【{user_id}】当前URL: {page.url}")
                        logger.info(f"【{user_id}】页面标题: {page.title()}")
                        logger.info(f"【{user_id}】=====================================")
                        
                        # 检查iframe并查找登录frame
                        iframes = page.query_selector_all('iframe')
                        logger.info(f"【{user_id}】找到 {len(iframes)} 个 iframe")
                        
                        # 查找包含登录元素的frame
                        login_selectors = [
                            'a.password-login-tab-item',  # 密码登录标签
                            '#fm-login-id',  # 账号输入框
                            '#fm-login-password',  # 密码输入框
                        ]
                        
                        login_frame = _find_frame_with_login(page, login_selectors, user_id)
                        if not login_frame:
                            logger.error(f"【{user_id}】✗ 未找到登录frame")
                            return None
                        
                        logger.info(f"【{user_id}】✓ 找到登录frame，开始登录流程")
                        
                        # 查找密码登录标签
                        logger.info(f"【{user_id}】查找密码登录标签...")
                        try:
                            password_tab = login_frame.query_selector('a.password-login-tab-item')
                            if password_tab:
                                logger.info(f"【{user_id}】✓ 找到密码登录标签，点击中...")
                                password_tab.click()
                                time.sleep(1.5)
                        except Exception as e:
                            logger.warning(f"【{user_id}】查找密码登录标签失败: {e}")
                        
                        # 输入账号
                        logger.info(f"【{user_id}】输入账号: {account}")
                        time.sleep(1)
                        
                        account_input = login_frame.query_selector('#fm-login-id')
                        if account_input:
                            logger.info(f"【{user_id}】✓ 找到账号输入框")
                            account_input.fill(account)
                            logger.info(f"【{user_id}】✓ 账号已输入")
                            time.sleep(random.uniform(0.5, 1.0))
                        else:
                            logger.error(f"【{user_id}】✗ 未找到账号输入框")
                            return None
                        
                        # 输入密码
                        logger.info(f"【{user_id}】输入密码...")
                        password_input = login_frame.query_selector('#fm-login-password')
                        if password_input:
                            password_input.fill(password)
                            logger.info(f"【{user_id}】✓ 密码已输入")
                            time.sleep(random.uniform(0.5, 1.0))
                        else:
                            logger.error(f"【{user_id}】✗ 未找到密码输入框")
                            return None
                        
                        # 查找并勾选用户协议
                        logger.info(f"【{user_id}】查找并勾选用户协议...")
                        try:
                            agreement_checkbox = login_frame.query_selector('#fm-agreement-checkbox')
                            if agreement_checkbox:
                                is_checked = agreement_checkbox.evaluate('el => el.checked')
                                if not is_checked:
                                    agreement_checkbox.click()
                                    time.sleep(0.3)
                                    logger.info(f"【{user_id}】✓ 用户协议已勾选")
                        except Exception as e:
                            logger.warning(f"【{user_id}】勾选用户协议失败: {e}")
                        
                        # 点击登录按钮
                        logger.info(f"【{user_id}】点击登录按钮...")
                        time.sleep(1)
                        
                        login_button = login_frame.query_selector('button.password-login')
                        if login_button:
                            logger.info(f"【{user_id}】✓ 找到登录按钮")
                            login_button.click()
                            logger.info(f"【{user_id}】✓ 登录按钮已点击")
                        else:
                            logger.error(f"【{user_id}】✗ 未找到登录按钮")
                            return None
                        
                        # 点击登录后，持续监控滑块和验证状态
                        logger.info(f"【{user_id}】========== 登录后监控 ==========")
                        logger.info(f"【{user_id}】开始监控滑块验证和登录状态...")
                        
                        # 监控参数
                        monitor_interval = 2  # 每2秒检查一次
                        max_monitor_time = 60  # 最多监控60秒
                        monitor_elapsed = 0
                        slider_handled = False
                        login_verified = False
                        slider_fail_count = 0  # 滑块失败计数（用于跟踪，但每次都是尝试5次）
                        page_refresh_count = 0  # 页面刷新次数
                        max_page_refreshes = 3  # 最多刷新3次
                        
                        while monitor_elapsed < max_monitor_time and not login_verified:
                            # 第一次循环不等待，立即检测
                            if monitor_elapsed > 0:
                                time.sleep(monitor_interval)
                            
                            monitor_elapsed += monitor_interval
                            logger.debug(f"【{user_id}】监控中... 已用时: {monitor_elapsed}秒")
                            
                            # 0. 优先检测滑块验证（最重要，响应最快）
                            if not slider_handled:
                                has_slider, slider_frame = _detect_slider_verification_in_page(page, user_id)
                                
                                if has_slider and slider_frame:
                                    logger.warning(f"【{user_id}】🔍 检测到滑块验证！立即处理... (页面刷新次数: {page_refresh_count}/{max_page_refreshes})")
                                    
                                    # 等待滑块完全加载
                                    logger.info(f"【{user_id}】等待滑块元素完全加载...")
                                    time.sleep(2)
                                    
                                    # 处理滑块（传入找到的frame，最多尝试5次）
                                    slider_success = _handle_slider_verification(slider_frame, user_id, max_attempts=5)
                                    
                                    if slider_success:
                                        logger.success(f"【{user_id}】✅ 滑块验证处理成功")
                                        slider_handled = True
                                        slider_fail_count = 0  # 重置失败计数
                                        time.sleep(2)
                                    else:
                                        # 滑块验证失败（已经尝试了5次），立即刷新页面重试
                                        logger.error(f"【{user_id}】❌ 滑块验证失败（已尝试5次）")
                                        
                                        # 检查是否还可以刷新页面
                                        if page_refresh_count < max_page_refreshes:
                                            page_refresh_count += 1
                                            logger.warning(f"【{user_id}】⚠️ 滑块验证失败，刷新页面重试 (第{page_refresh_count}/{max_page_refreshes}次刷新)")
                                            
                                            try:
                                                # 刷新页面
                                                page.reload(wait_until='domcontentloaded', timeout=30000)
                                                logger.info(f"【{user_id}】✓ 页面已刷新")
                                                time.sleep(3)
                                                
                                                # 重置滑块失败计数
                                                slider_fail_count = 0
                                                # 重置slider_handled标志，允许重新检测滑块
                                                slider_handled = False
                                                
                                                # 重新查找登录frame并填写信息
                                                logger.info(f"【{user_id}】重新查找登录frame...")
                                                login_frame = _find_frame_with_login(page, [
                                                    'a.password-login-tab-item',
                                                    '#fm-login-id',
                                                    '#fm-login-password'
                                                ], user_id)
                                                
                                                if not login_frame:
                                                    logger.error(f"【{user_id}】刷新后未找到登录frame")
                                                    break
                                                
                                                # 重新填写账号密码
                                                logger.info(f"【{user_id}】重新填写登录信息...")
                                                
                                                # 点击密码登录标签
                                                try:
                                                    password_tab = login_frame.query_selector('a.password-login-tab-item')
                                                    if password_tab:
                                                        password_tab.click()
                                                        time.sleep(1)
                                                except:
                                                    pass
                                                
                                                # 填写账号
                                                account_input = login_frame.query_selector('#fm-login-id')
                                                if account_input:
                                                    account_input.fill('')
                                                    time.sleep(0.2)
                                                    account_input.fill(account)
                                                    time.sleep(0.5)
                                                
                                                # 填写密码
                                                password_input = login_frame.query_selector('#fm-login-password')
                                                if password_input:
                                                    password_input.fill('')
                                                    time.sleep(0.2)
                                                    password_input.fill(password)
                                                    time.sleep(0.5)
                                                
                                                # 勾选协议
                                                try:
                                                    agreement_checkbox = login_frame.query_selector('#fm-agreement-checkbox')
                                                    if agreement_checkbox:
                                                        is_checked = agreement_checkbox.evaluate('el => el.checked')
                                                        if not is_checked:
                                                            agreement_checkbox.click()
                                                            time.sleep(0.3)
                                                except:
                                                    pass
                                                
                                                # 点击登录按钮
                                                login_button = login_frame.query_selector('button.password-login')
                                                if login_button:
                                                    login_button.click()
                                                    logger.info(f"【{user_id}】✓ 重新点击登录按钮")
                                                    time.sleep(3)
                                                else:
                                                    logger.error(f"【{user_id}】未找到登录按钮")
                                                    break
                                                
                                                # 重置监控时间，继续监控
                                                monitor_elapsed = 0
                                                
                                            except Exception as refresh_err:
                                                logger.error(f"【{user_id}】刷新页面失败: {refresh_err}")
                                                break
                                        else:
                                            logger.error(f"【{user_id}】❌ 已达到最大刷新次数({max_page_refreshes})，停止尝试")
                                            break
                            
                            # 1. 检查账密错误
                            try:
                                has_error, error_message = _check_login_error(page, user_id)
                                if has_error:
                                    logger.error(f"【{user_id}】❌❌❌ 登录失败：{error_message} ❌❌❌")
                                    logger.error(f"【{user_id}】请检查账号和密码是否正确！")
                                    
                                    # 发送通知
                                    try:
                                        notification_title = "闲鱼登录失败"
                                        notification_message = (
                                            f"❌ 登录失败 - 账号密码错误\n\n"
                                            f"账号ID: {user_id}\n"
                                            f"错误信息: {error_message}\n"
                                            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                                            f"请检查账号和密码是否正确，然后重新配置。"
                                        )
                                        send_notification(user_id, notification_title, notification_message, "error")
                                    except Exception as notify_err:
                                        logger.warning(f"【{user_id}】发送账密错误通知失败: {notify_err}")
                                    
                                    # 停止监控，返回失败
                                    return None
                                
                            except Exception as e:
                                logger.debug(f"【{user_id}】检查账密错误时出错: {e}")
                            
                            # 2. 检查登录状态（通过页面元素）
                            try:
                                if _check_login_success_by_element(page, user_id):
                                    logger.success(f"【{user_id}】✅ 登录验证成功！")
                                    login_verified = True
                                    break
                            except Exception as e:
                                logger.debug(f"【{user_id}】检查登录状态时出错: {e}")
                        
                        if login_verified:
                            logger.success(f"【{user_id}】✅ 登录流程完成，跳过额外等待")
                        else:
                            logger.warning(f"【{user_id}】⚠️ 监控超时，检查是否需要额外验证")
                        
                        # 如果还未登录成功，检测二维码/人脸验证
                        has_qr = False
                        qr_frame = None
                        if not login_verified:
                            has_qr, qr_frame = _detect_qr_code_verification(page, user_id)
                        
                        if has_qr and not login_verified:
                            logger.warning(f"【{user_id}】⚠️ 检测到二维码/人脸验证")
                            logger.info(f"【{user_id}】请在浏览器中完成二维码/人脸验证")
                            
                            # 获取并显示二维码链接
                            qr_url = None
                            if qr_frame:
                                try:
                                    frame_url = qr_frame.url
                                    qr_url = frame_url
                                    logger.warning(f"【{user_id}】" + "=" * 60)
                                    logger.warning(f"【{user_id}】二维码/人脸验证链接:")
                                    logger.warning(f"【{user_id}】{frame_url}")
                                    logger.warning(f"【{user_id}】" + "=" * 60)
                                    logger.info(f"【{user_id}】请在浏览器中完成验证，程序将持续等待...")
                                except Exception as e:
                                    logger.debug(f"【{user_id}】获取frame URL失败: {e}")
                            
                            # 发送通知
                            if qr_url:
                                try:
                                    _send_qr_verification_notification(user_id, qr_url)
                                except Exception as e:
                                    logger.warning(f"【{user_id}】发送二维码验证通知失败: {e}")
                            
                            # 持续等待用户完成二维码/人脸验证
                            logger.info(f"【{user_id}】等待二维码/人脸验证完成...")
                            check_interval = 10  # 每10秒检查一次
                            
                            while True:
                                time.sleep(check_interval)
                                
                                # 检查登录状态（通过页面元素）
                                try:
                                    if _check_login_success_by_element(page, user_id):
                                        logger.success(f"【{user_id}】✅ 验证成功，登录状态已确认！")
                                        break
                                    else:
                                        logger.info(f"【{user_id}】等待验证中... (每{check_interval}秒检查一次)")
                                except Exception as e:
                                    logger.debug(f"【{user_id}】检查登录状态时出错: {e}")
                            
                            logger.info(f"【{user_id}】二维码/人脸验证已完成")
                            login_verified = True  # 标记为已验证
                        elif not login_verified:
                            logger.info(f"【{user_id}】未检测到二维码/人脸验证")
                            
                            # 直接检查登录状态，不等待5分钟
                            logger.info(f"【{user_id}】直接检查登录状态...")
                            
                            try:
                                if _check_login_success_by_element(page, user_id):
                                    logger.success(f"【{user_id}】✅ 登录验证成功！")
                                    login_verified = True
                                else:
                                    logger.error(f"【{user_id}】❌ 未检测到登录成功")
                                    logger.error(f"【{user_id}】登录失败，请检查账号密码或网络状态")
                                    return None
                            except Exception as e:
                                logger.error(f"【{user_id}】检查登录状态时出错: {e}")
                                logger.error(f"【{user_id}】登录失败")
                                return None
                        else:
                            logger.info(f"【{user_id}】登录验证完成，开始获取Cookie...")
                        
                        logger.info(f"【{user_id}】======================================")
                        
                        # 检查登录后的URL和标题
                        logger.info(f"【{user_id}】登录后URL: {page.url}")
                        logger.info(f"【{user_id}】登录后页面标题: {page.title()}")
                        
                        # 获取Cookie
                        cookies_dict = {}
                        try:
                            cookies_list = context.cookies()
                            for cookie in cookies_list:
                                cookies_dict[cookie.get('name', '')] = cookie.get('value', '')
                            
                            logger.info(f"【{user_id}】成功获取Cookie，包含 {len(cookies_dict)} 个字段")
                            
                            # 打印关键Cookie字段
                            important_keys = ['unb', '_m_h5_tk', '_m_h5_tk_enc', 'cookie2', 't', 'sgcookie', 'cna']
                            logger.info(f"【{user_id}】关键Cookie字段检查:")
                            for key in important_keys:
                                if key in cookies_dict:
                                    val = cookies_dict[key]
                                    logger.info(f"【{user_id}】  ✅ {key}: {'存在' if val else '为空'} (长度: {len(str(val)) if val else 0})")
                                else:
                                    logger.info(f"【{user_id}】  ❌ {key}: 缺失")
                            
                            logger.info("=" * 60)
                            
                            # 验证登录状态（通过页面元素）
                            if cookies_dict:
                                logger.info(f"【{user_id}】正在验证登录状态...")
                                if _check_login_success_by_element(page, user_id):
                                    logger.success("✅ 登录成功！Cookie有效")
                                    logger.info(f"获取到 {len(cookies_dict)} 个Cookie字段")
                                    
                                    # 生成Cookie字符串
                                    cookie_str = '; '.join([f"{k}={v}" for k, v in cookies_dict.items()])
                                    logger.info(f"Cookie字符串:" + cookie_str)
                                    return cookies_dict
                                else:
                                    logger.error("❌ 登录验证失败，页面元素未找到")
                                    logger.warning("可能原因：1) 登录未完成 2) 需要额外验证 3) 页面未加载完成")
                                    return None
                            else:
                                logger.error("❌ 未获取到Cookie")
                                return None
                        except Exception as e:
                            logger.error(f"【{user_id}】获取Cookie失败: {e}")
                            return None
                    
                    except Exception as page_e:
                        logger.error(f"【{user_id}】页面操作出错: {page_e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        return None
                
                finally:
                    # 关闭浏览器
                    try:
                        if show_browser:
                            logger.info(f"【{user_id}】有头模式：保持浏览器打开，等待手动关闭...")
                            logger.info(f"【{user_id}】关闭浏览器后，缓存将自动保存到: {user_data_dir}")
                            # 不关闭浏览器，让用户手动关闭
                        else:
                            context.close()
                            playwright.stop()
                            logger.info(f"【{user_id}】无头模式：浏览器已关闭，缓存已保存")
                    except Exception as e:
                        logger.warning(f"【{user_id}】关闭浏览器时出错: {e}")
                        try:
                            playwright.stop()
                        except:
                            pass
            
            except Exception as e:
                logger.error(f"【{user_id}】密码登录流程异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                
                # 如果出错，尝试回退到原始方法
                if original_method:
                    logger.info(f"【{user_id}】尝试回退到原始方法")
                    try:
                        return original_method(self, account, password, show_browser)
                    except:
                        pass
                
                return None
        
        # 替换方法
        XianyuSliderStealth.login_with_password_headful = patched_login_with_password_headful
        
        logger.info("✓ login_with_password_headful 方法已通过猴子补丁替换（使用Playwright）")
        return True
        
    except ImportError as e:
        logger.error(f"无法导入 xianyu_slider_stealth 模块: {e}")
        return False
    except Exception as e:
        logger.error(f"应用 login_with_password_headful 补丁失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def patch_check_date_validity_force_allow():
    """追加一个干净补丁，确保日期校验永久放行。"""
    try:
        try:
            from utils.xianyu_slider_stealth import XianyuSliderStealth
        except ImportError:
            from xianyu_slider_stealth import XianyuSliderStealth

        def always_allow(self) -> bool:
            pure_user_id = getattr(self, 'pure_user_id', getattr(self, 'user_id', 'unknown'))
            logger.info(f"【{pure_user_id}】日期校验已禁用，直接放行")
            return True

        XianyuSliderStealth._check_date_validity = always_allow
        logger.info("✓ 追加日期校验放行补丁已生效")
        return True
    except Exception as e:
        logger.error(f"追加日期校验放行补丁失败: {e}")
        return False


def apply_patches():
    """
    应用所有补丁
    在程序启动时调用此函数
    """
    logger.info("开始应用滑块验证模块补丁...")
    patch_check_date_validity()
    patch_check_date_validity_force_allow()
    # patch_simulate_slide() 已禁用：xianyu_slider_stealth.py 中的原版 simulate_slide 已优化
    # 包含 Perlin 噪声轨迹、用户速度人格、服务端判定等待等改进，不再需要猴子补丁覆盖
    patch_login_with_password_headful()  # 重写密码登录方法
    logger.info("滑块验证模块补丁应用完成")


# 自动应用补丁（如果直接导入此模块）
if __name__ != "__main__":
    try:
        # 尝试自动应用补丁
        apply_patches()
    except:
        pass  # 如果导入失败，忽略错误
