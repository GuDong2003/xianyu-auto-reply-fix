import json
import time
import os
import sys
import re
import hashlib
import base64
import struct
import math
from typing import Any, Dict, List
import requests
from loguru import logger
import asyncio

import time
import random
from loguru import logger
from DrissionPage import Chromium, ChromiumOptions

def log_captcha_event(cookie_id: str, event_type: str, success: bool = None, details: str = ""):
    """简单记录滑块验证事件到txt文件"""
    try:
        import os
        log_dir = 'logs'
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'captcha_verification.txt')

        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        status = "成功" if success is True else "失败" if success is False else "进行中"

        log_entry = f"[{timestamp}] 【{cookie_id}】{event_type} - {status}"
        if details:
            log_entry += f" - {details}"
        log_entry += "\n"

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)

    except Exception as e:
        logger.error(f"记录滑块验证日志失败: {e}")


class DrissionHandler:
    def __init__(
            self, max_retries: int = 3, is_headless: bool = False, maximize_window: bool = True, show_mouse_trace: bool = True
    ):
        """
        初始化 Drission 浏览器
        :param max_retries: 最大重试次数
        :param is_headless: 是否开启无头浏览器
        :param maximize_window: 是否最大化窗口（推荐开启以提高滑块通过率）
        :param show_mouse_trace: 是否显示鼠标轨迹（调试用）
        """
        self.max_retries = max_retries  # 失败时的最大重试次数
        self.slide_attempt = 0  # 当前滑动尝试次数
        self.maximize_window = maximize_window
        self.show_mouse_trace = show_mouse_trace  # 鼠标轨迹可视化

        # 🎯 垂直偏移量配置（可调整）
        self.y_drift_range = 3      # 整体漂移趋势范围 ±3像素（原来是±8）
        self.shake_range = 1.5      # 基础抖动范围 ±1.5像素（原来是±3）
        self.fast_move_multiplier = 1.8  # 快速移动时的抖动放大倍数（原来是2.5）
        self.directional_range = 1.0     # 方向性偏移范围（原来是2.0）
        self.max_y_offset = 8       # 最大垂直偏移限制 ±8像素（原来是±15）

        self.co = ChromiumOptions()

        # 根据操作系统设置浏览器路径
        import platform
        system = platform.system().lower()
        if system == "linux":
            # Linux系统
            possible_paths = [
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable"
            ]
            browser_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    browser_path = path
                    break
            if browser_path:
                self.co.set_browser_path(browser_path)
                logger.debug(f"使用浏览器路径: {browser_path}")
            else:
                logger.warning("未找到可用的浏览器路径，使用默认设置")
        elif system == "windows":
            # Windows系统，通常不需要手动设置路径
            logger.debug("Windows系统，使用默认浏览器路径")
        else:
            # macOS或其他系统
            logger.debug(f"检测到系统: {system}，使用默认浏览器路径")

        # 设置端口，避免端口冲突
        self.co.set_argument("--remote-debugging-port=0")  # 让系统自动分配端口

        self.co.set_argument("--no-sandbox")  # 运行无沙盒
        self.co.new_env(True)  # 创建新的浏览器环境
        self.co.no_imgs(True)  # 禁用图片加载
        self.co.headless(on_off=is_headless)  # 是否开启无头模式

        # 添加更多稳定性参数
        self.co.set_argument("--disable-dev-shm-usage")
        self.co.set_argument("--disable-gpu")
        self.co.set_argument("--disable-web-security")
        self.co.set_argument("--disable-features=VizDisplayCompositor")
        self.co.set_argument("--disable-blink-features=AutomationControlled")  # 隐藏自动化特征
        self.co.set_argument("--disable-extensions")  # 禁用扩展
        self.co.set_argument("--no-first-run")  # 跳过首次运行设置
        self.co.set_argument("--disable-default-apps")  # 禁用默认应用

        # 添加更多兼容性参数
        self.co.set_argument("--disable-background-timer-throttling")
        self.co.set_argument("--disable-renderer-backgrounding")
        self.co.set_argument("--disable-backgrounding-occluded-windows")
        self.co.set_argument("--disable-ipc-flooding-protection")

        # 如果需要最大化窗口，设置启动参数
        if maximize_window and not is_headless:
            # 设置最大化启动参数
            self.co.set_argument("--start-maximized")        # 启动时最大化
            self.co.set_argument("--window-size=1920,1080")  # 设置大尺寸作为备用
            self.co.set_argument("--force-device-scale-factor=1")  # 强制缩放比例
            self.co.set_argument("--disable-features=TranslateUI")  # 禁用可能影响窗口的功能
            logger.info("已设置浏览器最大化启动参数")
        elif is_headless:
            # 无头模式下设置一个常见的桌面分辨率
            self.co.set_argument("--window-size=1920,1080")
            # 模拟真实设备特征
            self.co.set_argument("--force-device-scale-factor=1")

        try:
            logger.info("正在启动浏览器...")

            # 尝试多种启动方式
            browser_started = False

            # 方式1: 使用自定义配置
            try:
                self.browser = Chromium(self.co)
                browser_started = True
                logger.info("浏览器启动成功（自定义配置）")
            except Exception as e1:
                logger.warning(f"自定义配置启动失败: {e1}")

                # 方式2: 使用默认配置
                try:
                    logger.info("尝试使用默认配置启动浏览器...")
                    self.browser = Chromium()
                    browser_started = True
                    logger.info("浏览器启动成功（默认配置）")
                except Exception as e2:
                    logger.error(f"默认配置启动也失败: {e2}")
                    raise Exception(f"所有启动方式都失败。自定义配置错误: {e1}，默认配置错误: {e2}")

            if browser_started:
                self.page = self.browser.latest_tab  # 获取最新标签页
                logger.info("获取浏览器标签页成功")

                # 如果是有头模式且需要最大化，在浏览器启动后再次确保最大化
                if maximize_window and not is_headless:
                    import time
                    logger.info("正在最大化浏览器窗口...")

                    # 等待浏览器完全启动
                    time.sleep(1)

                    max_attempts = 3
                    for attempt in range(max_attempts):
                        try:
                            logger.info(f"最大化尝试 {attempt + 1}/{max_attempts}...")

                            # 方法1: 先设置窗口位置到左上角
                            try:
                                self.page.set.window.location(0, 0)
                                time.sleep(0.2)
                            except:
                                pass

                            # 方法2: 设置一个大尺寸
                            try:
                                self.page.set.window.size(1920, 1080)
                                time.sleep(0.3)
                            except:
                                pass

                            # 方法3: 执行最大化
                            self.page.set.window.max()
                            time.sleep(0.5)

                            # 方法4: 使用JavaScript强制最大化
                            try:
                                self._javascript_maximize()
                            except Exception as js_e:
                                logger.debug(f"JavaScript最大化失败: {js_e}")

                            # 方法5: 如果是Windows系统，尝试使用系统API强制最大化
                            try:
                                import platform
                                if platform.system() == "Windows":
                                    self._force_maximize_windows()
                            except Exception as win_e:
                                logger.debug(f"Windows API最大化失败: {win_e}")

                            # 验证最大化结果
                            try:
                                current_size = self.page.size
                                current_pos = self.page.location
                                logger.info(f"窗口尺寸: {current_size[0]}x{current_size[1]}, 位置: ({current_pos[0]}, {current_pos[1]})")

                                # 判断是否成功最大化
                                if current_size[0] >= 1400 and current_size[1] >= 900:
                                    logger.info("✅ 浏览器窗口最大化成功！")
                                    break
                                elif attempt == max_attempts - 1:
                                    logger.warning(f"⚠️ 窗口尺寸较小: {current_size[0]}x{current_size[1]}")
                                    logger.info("继续使用当前窗口尺寸...")
                                else:
                                    logger.info(f"尺寸不够大，进行第 {attempt + 2} 次尝试...")

                            except Exception as check_e:
                                logger.warning(f"检查窗口状态失败: {check_e}")
                                if attempt == max_attempts - 1:
                                    logger.info("无法验证窗口状态，继续执行...")

                        except Exception as max_e:
                            logger.warning(f"第 {attempt + 1} 次最大化失败: {max_e}")
                            if attempt == max_attempts - 1:
                                logger.warning("所有最大化尝试都失败，使用默认窗口尺寸")
                            else:
                                time.sleep(0.5)  # 等待后重试

                # 如果启用鼠标轨迹可视化，注入CSS和JavaScript
                if self.show_mouse_trace and not is_headless:
                    self._inject_mouse_trace_visualization()

        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            raise

        self.cookies = {}
        self.Refresh = False

    def set_cookies_from_string(self, cookies_str: str):
        """从cookies字符串设置cookies到浏览器"""
        try:
            if not cookies_str:
                logger.warning("cookies字符串为空，跳过设置")
                return

            # 解析cookies字符串
            cookies_dict = {}
            for cookie_pair in cookies_str.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies_dict[name.strip()] = value.strip()

            # 设置cookies到浏览器
            for name, value in cookies_dict.items():
                try:
                    self.page.set.cookies({
                        'name': name,
                        'value': value,
                        'domain': '.goofish.com',
                        'path': '/'
                    })
                except Exception as e:
                    logger.debug(f"设置cookie失败 {name}: {e}")

            logger.info(f"已设置 {len(cookies_dict)} 个cookies到浏览器")
            self.cookies = cookies_dict

        except Exception as e:
            logger.error(f"设置cookies时出错: {e}")

    def get_cookies_string(self) -> str:
        """获取当前浏览器的cookies并转换为字符串格式"""
        try:
            # 获取浏览器中的所有cookies
            browser_cookies = self.page.cookies()

            # 转换为字符串格式
            cookie_pairs = []
            for cookie in browser_cookies:
                if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                    cookie_pairs.append(f"{cookie['name']}={cookie['value']}")

            cookies_str = '; '.join(cookie_pairs)
            logger.info(f"获取到 {len(cookie_pairs)} 个cookies")
            return cookies_str

        except Exception as e:
            logger.error(f"获取cookies字符串时出错: {e}")
            return ""

    def _slide(self):
        """处理滑动验证码"""
        try:
            allowed_countries = {
                item.strip().upper()
                for item in os.environ.get('XY_SLIDER_ALLOWED_COUNTRIES', 'CN').split(',')
                if item.strip()
            }
            if allowed_countries:
                try:
                    geo_url = os.environ.get('XY_SLIDER_GEO_URL', 'https://api.country.is/').strip()
                    country = str(requests.get(geo_url, timeout=5).json().get('country') or '').upper()
                    if country not in allowed_countries:
                        self.country_gate_blocked = True
                        logger.warning(
                            f"订单详情滑块已被国家门控阻止: 当前出口国家为 {country or '未知'}，"
                            f"允许范围为 {','.join(sorted(allowed_countries))}"
                        )
                        return
                except Exception as geo_error:
                    if os.environ.get('XY_SLIDER_GEO_FAIL_OPEN', '0').lower() not in {'1', 'true', 'yes', 'on'}:
                        self.country_gate_blocked = True
                        logger.warning(f"订单详情滑块国家检查失败，已安全阻止: {geo_error}")
                        return

            self.slide_attempt += 1
            logger.info(f"尝试处理滑动验证码... (第{self.slide_attempt}次)")

            # 根据循环策略调整行为模式
            cycle_position = (self.slide_attempt - 1) % 3
            is_impatient = cycle_position == 1  # 急躁快速阶段
            is_reflective = cycle_position == 2  # 反思调整阶段

            # 记录滑块验证尝试到日志文件
            strategy_name = ""
            if cycle_position == 0:
                strategy_name = "谨慎慢速模式"
            elif cycle_position == 1:
                strategy_name = "急躁快速模式"
            else:
                strategy_name = "反思调整模式"

            # 获取cookie_id（如果可用）
            cookie_id = getattr(self, 'cookie_id', 'unknown')

            log_captcha_event(cookie_id, f"滑块验证尝试(第{self.slide_attempt}次)", None, f"策略: {strategy_name}")
            
            ele = self.page.wait.eles_loaded(
                "x://span[contains(@id,'nc_1_n1z')]", timeout=10
            )
            if ele:
                slider = self.page.ele("#nc_1_n1z")  # 滑块
                
                # 根据尝试次数调整观察时间
                if is_impatient:
                    # 急躁模式：观察时间大幅缩短
                    observation_time = random.uniform(0.1, 0.5)
                    logger.info("急躁模式：快速开始滑动")
                else:
                    # 正常模式：仔细观察
                    observation_time = random.uniform(0.8, 2.5)
                    logger.info("正常模式：仔细观察")
                
                time.sleep(observation_time)
                
                # 严谨的鼠标模拟活动
                try:
                    logger.info("开始严谨的鼠标模拟活动...")
                    
                    # 第一阶段：页面进入行为模拟
                    self._simulate_page_entry()
                    
                    # 第二阶段：寻找验证码过程模拟
                    self._simulate_looking_for_captcha()
                    
                    # 第三阶段：接近滑块的自然移动
                    self._simulate_approaching_slider(slider)
                    
                    # 第四阶段：操作滑块
                    if is_impatient:
                        # 急躁模式：快速操作
                        slider.hover()
                        time.sleep(random.uniform(0.02, 0.08))
                        self.page.actions.hold(slider)
                        time.sleep(random.uniform(0.02, 0.1))
                    else:
                        # 正常模式：谨慎操作
                        slider.hover()
                        time.sleep(random.uniform(0.1, 0.3))
                        self.page.actions.hold(slider)
                        time.sleep(random.uniform(0.1, 0.4))
                        
                except Exception as hover_error:
                    logger.warning(f"滑块hover失败: {hover_error}，尝试直接hold")
                    try:
                        self.page.actions.hold(slider)
                        time.sleep(random.uniform(0.1, 0.3))
                    except Exception as hold_error:
                        logger.error(f"滑块hold失败: {hold_error}")
                        return

                # 智能循环策略：快→慢→中等循环
                import time as time_module
                random.seed(int(time_module.time() * 1000000) % 1000000)  # 使用微秒作为随机种子
                
                # 计算当前策略阶段（3次一个循环）
                cycle_position = (self.slide_attempt - 1) % 3
                cycle_number = (self.slide_attempt - 1) // 3 + 1
                
                # 判断是否需要刷新页面（每轮开始时的谨慎模式考虑刷新）
                if cycle_position == 0 and cycle_number > 1:  # 从第二轮开始的谨慎模式
                    refresh_probability = min(0.2 + (cycle_number - 2) * 0.15, 0.7)  # 概率递增
                    if random.random() < refresh_probability:
                        self.Refresh = True
                        logger.info(f"第{cycle_number}轮开始 - 计划刷新页面重试 (概率: {refresh_probability:.2f})")
                    else:
                        self.Refresh = False
                
                if cycle_position == 0:  # 第1、4、7...次：谨慎慢速
                    if cycle_number == 1:
                        # 第一轮：最谨慎
                        target_total_time = random.uniform(2.0, 4.0)
                        trajectory_points = random.randint(80, 150)
                        sliding_mode = "初次谨慎模式"
                    else:
                        # 后续轮：谨慎但稍快
                        target_total_time = random.uniform(1.5, 3.0)
                        trajectory_points = random.randint(60, 120)
                        sliding_mode = f"第{cycle_number}轮谨慎模式" + (" [失败后将刷新]" if self.Refresh else "")
                        
                elif cycle_position == 1:  # 第2、5、8...次：急躁快速
                    base_speed = max(0.2, 1.0 - cycle_number * 0.1)  # 随轮次递减，但有底限
                    target_total_time = random.uniform(base_speed, base_speed + 0.4)
                    trajectory_points = random.randint(30, 60)
                    sliding_mode = f"第{cycle_number}轮急躁模式"
                    
                else:  # 第3、6、9...次：中等速度（反思调整）
                    target_total_time = random.uniform(1.0, 2.0)
                    trajectory_points = random.randint(50, 90)
                    sliding_mode = f"第{cycle_number}轮反思模式"
                
                # 根据策略生成对应数量的轨迹点
                # 动态计算滑动距离，适应不同分辨率
                base_distance = self._calculate_slide_distance()
                tracks = self.get_tracks(base_distance, target_points=trajectory_points)  # 传入目标点数
                
                logger.info(f"{sliding_mode} - 目标时间: {target_total_time:.2f}秒, 预设轨迹点: {trajectory_points}, 实际轨迹点: {len(tracks)}")
                
                # 记录实际开始时间
                actual_start_time = time.time()
                
                # 将绝对位置转换为相对移动距离
                for i in range(len(tracks)):
                    # 计算当前进度
                    progress = i / len(tracks)  # 当前进度 0-1
                    
                    if i == 0:
                        offset_x = tracks[i]  # 第一步是绝对位置
                    else:
                        offset_x = tracks[i] - tracks[i - 1]  # 后续是相对移动
                    
                    # 跳过零移动
                    if abs(offset_x) < 0.1:
                        continue
                    
                    # 更真实的垂直偏移模拟（使用可配置参数）
                    # 人类滑动时会有整体的向上或向下偏移趋势
                    if i == 1:  # 首次移动时确定整体偏移方向
                        self._slide_direction = random.choice([-1, 1])  # -1向上，1向下
                        self._cumulative_y_offset = 0
                        self._y_drift_trend = random.uniform(-self.y_drift_range, self.y_drift_range)  # 使用配置的漂移范围

                    # 基础垂直偏移：结合趋势和随机抖动
                    trend_offset = self._y_drift_trend * (progress ** 0.7)  # 逐渐累积的趋势偏移
                    shake_offset = random.uniform(-self.shake_range, self.shake_range)  # 使用配置的抖动范围
                    speed_influence = min(abs(offset_x) / 10.0, 2.0)  # 速度越快，抖动越大

                    # 人类在快速滑动时垂直偏移会更大
                    if abs(offset_x) > 8:  # 快速移动时
                        shake_offset *= random.uniform(1.2, self.fast_move_multiplier)  # 使用配置的放大倍数

                    # 整体偏移方向的影响
                    directional_offset = self._slide_direction * random.uniform(0.2, self.directional_range)  # 使用配置的方向偏移

                    offset_y = trend_offset + shake_offset + directional_offset

                    # 限制偏移范围，防止过度偏移
                    offset_y = max(-self.max_y_offset, min(self.max_y_offset, offset_y))  # 使用配置的最大偏移
                    
                    # 累积Y偏移，用于后续调整
                    self._cumulative_y_offset += offset_y
                    
                    # 基于目标总时间动态分配时间
                    # 计算剩余时间和剩余步骤
                    elapsed_time = time.time() - actual_start_time
                    remaining_time = max(target_total_time - elapsed_time, 0.1)
                    remaining_steps = len(tracks) - i
                    
                    # 基础时间分配
                    if remaining_steps > 0:
                        base_time_per_step = remaining_time / remaining_steps
                    else:
                        base_time_per_step = 0.01
                    
                    # 根据移动距离调整
                    distance_factor = max(abs(offset_x) / 15.0, 0.3)
                    base_duration = base_time_per_step * distance_factor * 0.7  # 70%用于移动duration
                    
                    # 更复杂的速度变化模拟
                    # 基于阶段的基础速度调整
                    if progress < 0.2:  # 起始阶段 - 谨慎启动
                        base_phase_multiplier = random.uniform(1.5, 2.5)
                    elif progress < 0.4:  # 加速阶段 - 逐渐加快
                        base_phase_multiplier = random.uniform(0.6, 1.2)
                    elif progress < 0.7:  # 快速阶段 - 相对快速
                        base_phase_multiplier = random.uniform(0.3, 0.8)
                    elif progress < 0.9:  # 减速阶段 - 开始减速
                        base_phase_multiplier = random.uniform(0.8, 1.8)
                    else:  # 精确阶段 - 谨慎定位
                        base_phase_multiplier = random.uniform(1.5, 3.0)
                    
                    # 添加速度突变（模拟人类的不均匀操作）
                    speed_burst_chance = 0.15  # 速度突变概率
                    if random.random() < speed_burst_chance:
                        if progress < 0.8:  # 前80%可能突然加速
                            burst_multiplier = random.uniform(0.2, 0.6)  # 突然加速
                        else:  # 后20%可能突然减速
                            burst_multiplier = random.uniform(2.0, 4.0)  # 突然减速
                    else:
                        burst_multiplier = 1.0
                    
                    # 基于移动距离的速度调整（大移动通常更快）
                    distance_speed_factor = 1.0
                    if abs(offset_x) > 10:  # 大距离移动
                        distance_speed_factor = random.uniform(0.4, 0.8)  # 更快
                    elif abs(offset_x) < 3:  # 小距离移动
                        distance_speed_factor = random.uniform(1.2, 2.0)  # 更慢
                    
                    # 添加周期性的速度波动（模拟手部节律）
                    rhythm_factor = 1 + 0.3 * math.sin(i * 0.5) * random.uniform(0.5, 1.5)
                    
                    # 综合所有速度因子
                    phase_multiplier = base_phase_multiplier * burst_multiplier * distance_speed_factor * rhythm_factor
                    
                    # 添加随机微调
                    random_variation = random.uniform(0.7, 1.3)
                    
                    final_duration = base_duration * phase_multiplier * random_variation
                    final_duration = max(0.005, min(0.15, final_duration))  # 限制在合理范围
                    
                    # 偶尔添加更长的停顿（模拟人类思考/调整），但要考虑剩余时间
                    if random.random() < 0.08 and progress > 0.2 and remaining_time > 0.5:
                        final_duration *= random.uniform(1.5, 2.5)
                    
                    # 根据急躁程度调整特殊行为
                    if not is_impatient:
                        # 正常模式：减少特殊行为频率和幅度
                        special_behavior_chance = random.random()
                        
                        if special_behavior_chance < 0.05 and progress > 0.4:  # 降低到5%概率
                            if progress < 0.8:  # 中途可能有小幅回退调整
                                # 小幅回退然后继续，减小幅度
                                retreat_distance = random.uniform(1, 3)  # 减小回退距离
                                try:
                                    self.page.actions.move(
                                        offset_x=int(-retreat_distance),
                                        offset_y=int(random.uniform(-0.5, 0.5)),
                                        duration=max(0.1, float(random.uniform(0.1, 0.2))),
                                    )
                                except Exception as retreat_error:
                                    logger.warning(f"回退动作失败: {retreat_error}")
                                    continue
                                time.sleep(random.uniform(0.02, 0.08))
                                # 继续原来的移动，补偿回退距离
                                offset_x += retreat_distance
                        
                        elif special_behavior_chance < 0.02 and progress > 0.6:  # 降低到2%概率暂停观察
                            # 短暂停顿（模拟观察缺口位置）
                            pause_time = random.uniform(0.1, 0.3)  # 减少停顿时间
                            time.sleep(pause_time)
                    else:
                        # 急躁模式：几乎不进行特殊行为
                        special_behavior_chance = random.random()
                        
                        if special_behavior_chance < 0.02 and progress > 0.6:  # 降低到2%概率
                            # 急躁的微小调整
                            retreat_distance = random.uniform(0.5, 1.5)  # 更小的调整
                            try:
                                self.page.actions.move(
                                    offset_x=int(-retreat_distance),
                                    offset_y=int(random.uniform(-0.2, 0.2)),
                                    duration=max(0.02, float(random.uniform(0.02, 0.05))),
                                )
                            except Exception as retreat_error:
                                logger.warning(f"急躁回退动作失败: {retreat_error}")
                                continue
                            time.sleep(random.uniform(0.01, 0.03))
                            offset_x += retreat_distance
                    
                    try:
                        self.page.actions.move(
                            offset_x=int(offset_x),  # 确保是整数
                            offset_y=int(offset_y),  # 确保是整数
                            duration=max(0.005, float(final_duration)),  # 确保是正数
                        )
                    except Exception as move_error:
                        logger.warning(f"滑动步骤失败: {move_error}，跳过此步骤")
                        continue
                    
                    # 动态调整步骤间延迟，确保总时间控制
                    remaining_delay_time = base_time_per_step * 0.3  # 30%用于延迟
                    step_delay = remaining_delay_time * random.uniform(0.5, 1.5)
                    step_delay = max(0.001, min(0.05, step_delay))  # 限制延迟范围
                    
                    # 根据进度调整延迟模式
                    if progress > 0.8:  # 接近结束时更谨慎
                        step_delay *= random.uniform(1.2, 2.0)
                    elif 0.3 < progress < 0.7:  # 中间阶段可能更快
                        step_delay *= random.uniform(0.6, 1.0)
                    
                    # 偶尔添加微停顿，但要考虑剩余时间
                    if random.random() < 0.05 and remaining_time > 0.3:
                        step_delay += random.uniform(0.005, 0.02)
                    
                    time.sleep(step_delay)

                # 滑动结束后继续保持鼠标在浏览器内活动
                try:
                    # 在释放前做一些自然的鼠标微动
                    micro_movements = random.randint(1, 3)
                    for i in range(micro_movements):
                        micro_x = random.uniform(-20, 20)
                        micro_y = random.uniform(-10, 10)
                        try:
                            self.page.actions.move(
                                offset_x=int(micro_x),
                                offset_y=int(micro_y),
                                duration=max(0.05, float(random.uniform(0.05, 0.15))),
                            )
                            time.sleep(random.uniform(0.02, 0.08))
                        except Exception as micro_error:
                            logger.warning(f"微动失败: {micro_error}")
                            break
                except Exception as micro_activity_error:
                    logger.warning(f"鼠标微动活动失败: {micro_activity_error}")
                
                # 根据急躁程度调整结束行为
                if is_impatient:
                    # 急躁模式：快速结束
                    final_adjustment_chance = random.random()
                    if final_adjustment_chance < 0.2:  # 只有20%概率微调
                        adjustment_distance = random.uniform(-2, 3)  # 更小的调整
                        try:
                            self.page.actions.move(
                                offset_x=int(adjustment_distance),
                                offset_y=int(random.uniform(-0.5, 0.5)),
                                duration=max(0.02, float(random.uniform(0.02, 0.1))),
                            )
                        except Exception as adjust_error:
                            logger.warning(f"急躁最终调整失败: {adjust_error}")
                        time.sleep(random.uniform(0.02, 0.08))  # 更短停顿
                    
                    # 急躁模式：确认停顿很短
                    confirmation_pause = random.uniform(0.05, 0.2)
                    time.sleep(confirmation_pause)
                    
                    self.page.actions.release()
                    
                    # 记录实际执行时间
                    actual_end_time = time.time()
                    actual_total_time = actual_end_time - actual_start_time
                    logger.info(f"急躁模式实际执行时间: {actual_total_time:.2f}秒, 目标时间: {target_total_time:.2f}秒")

                    # 急躁模式：释放后行为更少更快
                    if random.random() < 0.3:  # 只有30%概率
                        time.sleep(random.uniform(0.02, 0.1))
                        post_move_x = random.uniform(-3, 3)
                        post_move_y = random.uniform(-2, 2)
                        try:
                            self.page.actions.move(
                                offset_x=int(post_move_x),
                                offset_y=int(post_move_y),
                                duration=max(0.05, float(random.uniform(0.05, 0.2))),
                            )
                        except Exception as post_error:
                            logger.warning(f"急躁释放后移动失败: {post_error}")
                    
                    # 急躁模式：等待时间更短
                    time.sleep(random.uniform(0.1, 0.3))
                else:
                    # 正常模式：保持原有行为
                    final_adjustment_chance = random.random()
                    if final_adjustment_chance < 0.6:  # 60%概率进行最终微调
                        adjustment_distance = random.uniform(-3, 5)  # 略微超调或回调
                        try:
                            self.page.actions.move(
                                offset_x=int(adjustment_distance),
                                offset_y=int(random.uniform(-1, 1)),
                                duration=max(0.1, float(random.uniform(0.1, 0.3))),
                            )
                        except Exception as adjust_error:
                            logger.warning(f"正常最终调整失败: {adjust_error}")
                        time.sleep(random.uniform(0.1, 0.25))
                    
                    # 释放前的确认停顿（人类会确认位置正确）
                    confirmation_pause = random.uniform(0.2, 0.8)
                    time.sleep(confirmation_pause)
                    
                    self.page.actions.release()
                    
                    # 记录实际执行时间
                    actual_end_time = time.time()
                    actual_total_time = actual_end_time - actual_start_time
                    logger.info(f"正常模式实际执行时间: {actual_total_time:.2f}秒, 目标时间: {target_total_time:.2f}秒")

                    # 释放后的自然行为
                    post_release_behavior = random.random()
                    if post_release_behavior < 0.7:  # 70%概率有释放后行为
                        time.sleep(random.uniform(0.1, 0.3))
                        
                        post_move_x = random.uniform(-8, 8)
                        post_move_y = random.uniform(-5, 5)
                        try:
                            self.page.actions.move(
                                offset_x=int(post_move_x),
                                offset_y=int(post_move_y),
                                duration=max(0.2, float(random.uniform(0.2, 0.6))),
                            )
                        except Exception as post_error:
                            logger.warning(f"正常释放后移动失败: {post_error}")
                    
                    # 等待验证结果前的停顿
                    time.sleep(random.uniform(0.3, 0.8))
                
                # 验证完成后的严谨鼠标活动模拟
                self._simulate_post_verification_activity()

        except Exception as e:
            logger.error(f"滑动验证码处理失败: {e}")

    def _simulate_page_entry(self):
        """模拟用户刚进入页面时的鼠标行为"""
        try:
            logger.debug("模拟页面进入行为...")
            # 模拟从页面边缘进入的鼠标轨迹
            entry_movements = random.randint(3, 6)
            
            # 起始位置通常从页面边缘开始
            start_positions = [
                (-50, -30),   # 左上
                (50, -30),    # 右上
                (-30, 50),    # 左侧
                (30, 50),     # 右侧
            ]
            
            start_x, start_y = random.choice(start_positions)
            
            for i in range(entry_movements):
                # 逐渐向页面中心移动
                progress = (i + 1) / entry_movements
                target_x = start_x + (100 - start_x) * progress + random.uniform(-30, 30)
                target_y = start_y + (100 - start_y) * progress + random.uniform(-20, 20)
                
                # 添加人类鼠标移动的不完美性
                jitter_x = random.uniform(-5, 5)
                jitter_y = random.uniform(-5, 5)
                
                self.page.actions.move(
                    offset_x=int(target_x + jitter_x),
                    offset_y=int(target_y + jitter_y),
                    duration=random.uniform(0.15, 0.4)
                )
                time.sleep(random.uniform(0.1, 0.25))
                
        except Exception as e:
            logger.warning(f"页面进入模拟失败: {e}")
    
    def _simulate_looking_for_captcha(self):
        """模拟用户寻找验证码的鼠标行为"""
        try:
            logger.debug("模拟寻找验证码行为...")
            # 模拟扫视页面寻找验证码
            search_movements = random.randint(2, 4)
            
            for i in range(search_movements):
                # 模拟扫视不同区域
                if i == 0:
                    # 首先看页面上方
                    move_x = random.uniform(-100, 100)
                    move_y = random.uniform(-80, -20)
                elif i == 1:
                    # 然后看中间区域
                    move_x = random.uniform(-80, 80)
                    move_y = random.uniform(-20, 40)
                else:
                    # 最后聚焦到验证码区域
                    move_x = random.uniform(-60, 60)
                    move_y = random.uniform(20, 80)
                
                # 添加搜索时的停顿和小幅调整
                self.page.actions.move(
                    offset_x=int(move_x),
                    offset_y=int(move_y),
                    duration=random.uniform(0.2, 0.5)
                )
                time.sleep(random.uniform(0.3, 0.8))  # 模拟观察时间
                
                # 小幅度的调整移动
                if random.random() < 0.6:
                    self.page.actions.move(
                        offset_x=random.randint(-10, 10),
                        offset_y=random.randint(-8, 8),
                        duration=random.uniform(0.05, 0.15)
                    )
                    time.sleep(random.uniform(0.1, 0.3))
                    
        except Exception as e:
            logger.warning(f"寻找验证码模拟失败: {e}")
    
    def _simulate_approaching_slider(self, slider):
        """模拟用户接近滑块的自然移动过程"""
        try:
            logger.debug("模拟接近滑块行为...")
            
            # 分步骤接近滑块，而不是直接移动过去
            approach_steps = random.randint(2, 4)
            
            for i in range(approach_steps):
                progress = (i + 1) / approach_steps
                
                # 逐渐接近滑块的位置
                if i == 0:
                    # 第一步：大致移动到滑块附近
                    move_x = random.uniform(-100, -30)
                    move_y = random.uniform(-30, 30)
                elif i == approach_steps - 1:
                    # 最后一步：精确接近滑块
                    move_x = random.uniform(-20, 20)
                    move_y = random.uniform(-10, 10)
                else:
                    # 中间步骤：逐渐接近
                    move_x = random.uniform(-60, 0)
                    move_y = random.uniform(-20, 20)
                
                # 添加人类移动的不确定性
                hesitation = random.random() < 0.3  # 30%概率的犹豫
                
                if hesitation:
                    # 犹豫时会有小幅的来回移动
                    self.page.actions.move(
                        offset_x=int(move_x * 0.5),
                        offset_y=int(move_y * 0.5),
                        duration=random.uniform(0.1, 0.25)
                    )
                    time.sleep(random.uniform(0.05, 0.15))
                    
                    # 然后继续移动
                    self.page.actions.move(
                        offset_x=int(move_x * 0.5),
                        offset_y=int(move_y * 0.5),
                        duration=random.uniform(0.1, 0.25)
                    )
                else:
                    # 直接移动
                    self.page.actions.move(
                        offset_x=int(move_x),
                        offset_y=int(move_y),
                        duration=random.uniform(0.15, 0.35)
                    )
                
                time.sleep(random.uniform(0.1, 0.3))
                
        except Exception as e:
            logger.warning(f"接近滑块模拟失败: {e}")
    
    def _simulate_post_verification_activity(self):
        """模拟验证完成后的用户行为"""
        try:
            logger.debug("模拟验证后用户行为...")
            
            # 验证完成后的典型用户行为
            behaviors = [
                "check_result",    # 检查验证结果
                "move_away",       # 移开鼠标
                "return_focus",    # 回到原来关注的内容
                "scroll_check"     # 滚动查看
            ]
            
            selected_behaviors = random.sample(behaviors, random.randint(2, 3))
            
            for behavior in selected_behaviors:
                if behavior == "check_result":
                    # 短暂停留在验证区域
                    self.page.actions.move(
                        offset_x=random.randint(-20, 20),
                        offset_y=random.randint(-10, 10),
                        duration=random.uniform(0.1, 0.2)
                    )
                    time.sleep(random.uniform(0.5, 1.2))
                    
                elif behavior == "move_away":
                    # 将鼠标移到其他地方
                    self.page.actions.move(
                        offset_x=random.randint(-200, 200),
                        offset_y=random.randint(-100, 100),
                        duration=random.uniform(0.3, 0.6)
                    )
                    time.sleep(random.uniform(0.2, 0.5))
                    
                elif behavior == "return_focus":
                    # 回到页面中心或重要区域
                    self.page.actions.move(
                        offset_x=random.randint(-100, 100),
                        offset_y=random.randint(-80, 80),
                        duration=random.uniform(0.25, 0.5)
                    )
                    time.sleep(random.uniform(0.3, 0.8))
                    
                elif behavior == "scroll_check":
                    # 模拟滚动操作（如果支持）
                    try:
                        # 小幅滚动
                        scroll_amount = random.randint(-3, 3)
                        if scroll_amount != 0:
                            self.page.scroll(delta_y=scroll_amount * 100)
                            time.sleep(random.uniform(0.2, 0.6))
                    except:
                        # 如果滚动失败，就做个移动代替
                        self.page.actions.move(
                            offset_x=random.randint(-50, 50),
                            offset_y=random.randint(-30, 30),
                            duration=random.uniform(0.2, 0.4)
                        )
                        time.sleep(random.uniform(0.2, 0.5))
                        
        except Exception as e:
            logger.warning(f"验证后行为模拟失败: {e}")

    def ease_out_expo(self, t):
        """缓动函数，使滑动轨迹更自然"""
        return 1 - pow(2, -10 * t) if t != 1 else 1

    def get_tracks(self, distance, target_points=None):
        """
        生成更真实的人类滑动轨迹
        :param distance: 目标距离
        :param target_points: 目标轨迹点数，如果为None则自动计算
        :return: 绝对位置轨迹列表
        """
        tracks = []
        current = 0.0
        velocity = 0.0
        
        # 人类滑动特征参数
        max_velocity = random.uniform(80, 150)  # 最大速度
        acceleration_phase = distance * random.uniform(0.3, 0.6)  # 加速阶段占比
        deceleration_start = distance * random.uniform(0.6, 0.85)  # 减速开始位置
        
        # 根据目标点数动态调整时间步长
        if target_points:
            # 根据目标点数计算合适的时间步长
            base_dt = distance / (target_points * max_velocity * 0.5)  # 估算基础时间步长
            dt = base_dt * random.uniform(0.8, 1.2)  # 添加随机变化
            dt = max(0.01, min(0.2, dt))  # 限制在合理范围
        else:
            # 默认时间步长
            dt = random.uniform(0.02, 0.12)
        hesitation_probability = 0.15  # 犹豫概率
        overshoot_chance = 0.3  # 超调概率
        
        tracks.append(0)  # 起始位置
        
        step = 0
        hesitation_counter = 0
        
        while current < distance:
            step += 1
            
            # 人类滑动的三个阶段
            if current < acceleration_phase:
                # 加速阶段：逐渐加速，但不是线性的
                target_accel = random.uniform(15, 35)
                # 添加加速度的波动
                if step % random.randint(3, 8) == 0:
                    target_accel *= random.uniform(0.7, 1.4)
                
            elif current < deceleration_start:
                # 匀速阶段：保持相对稳定的速度，偶有波动
                target_accel = random.uniform(-2, 2)
                if random.random() < 0.2:  # 偶尔小幅调整速度
                    target_accel = random.uniform(-8, 8)
                    
            else:
                # 减速阶段：逐渐减速，接近目标时更加小心
                remaining_distance = distance - current
                if remaining_distance > 20:
                    target_accel = random.uniform(-25, -8)
                else:
                    # 接近目标时更加谨慎
                    target_accel = random.uniform(-15, -3)
            
            # 模拟人类的犹豫和调整 - 更真实的犹豫模式
            if random.random() < hesitation_probability and current > acceleration_phase:
                hesitation_counter += 1
                if hesitation_counter < 3:
                    # 犹豫时可能轻微后退或停顿
                    if random.random() < 0.4:
                        target_accel = random.uniform(-8, -2)  # 轻微后退
                    else:
                        target_accel = random.uniform(-2, 2)  # 停顿摇摆
                else:
                    hesitation_counter = 0
            
            # 更新速度，加入阻尼效果
            velocity = velocity * 0.95 + target_accel * dt
            velocity = max(0, min(velocity, max_velocity))  # 限制速度范围
            
            # 更新位置
            old_current = current
            current += velocity * dt
            
            # 添加手部微颤（高频小幅震动）
            if len(tracks) > 5:
                tremor = random.uniform(-0.3, 0.3) * (velocity / max_velocity)
                current += tremor
            
            # 添加更真实的人类修正行为
            if random.random() < 0.12 and current > 50:  # 增加修正频率
                correction_type = random.random()
                if correction_type < 0.6:  # 60%是小幅回退
                    current -= random.uniform(1.0, 4.0)
                elif correction_type < 0.8:  # 20%是停顿
                    pass  # 不移动，相当于停顿
                else:  # 20%是微调前进
                    current += random.uniform(0.2, 1.0)
            
            # 防止负向移动和过大跳跃
            if current < old_current:
                current = old_current + random.uniform(0.1, 0.8)
            
            if current - old_current > 15:  # 防止单步移动过大
                current = old_current + random.uniform(8, 15)
            
            tracks.append(round(current, 1))
        
        # 处理可能的超调
        if random.random() < overshoot_chance:
            # 轻微超调然后回调
            overshoot = random.uniform(2, 8)
            tracks.append(round(distance + overshoot, 1))
            
            # 回调过程
            correction_steps = random.randint(2, 5)
            for i in range(correction_steps):
                correction = overshoot * (1 - (i + 1) / correction_steps)
                noise = random.uniform(-0.3, 0.3)
                tracks.append(round(distance + correction + noise, 1))
        
        # 最终稳定阶段 - 减少调整次数
        final_adjustments = random.randint(1, 3)  # 减少最终调整次数
        target_final = distance + random.uniform(-1, 2)
        
        for i in range(final_adjustments):
            # 最终的细微调整
            adjustment = random.uniform(-0.5, 0.5)
            target_final += adjustment
            tracks.append(round(target_final, 1))
        
        # 清理和优化轨迹：减少冗余点
        cleaned_tracks = [tracks[0]]
        last_pos = tracks[0]
        
        for i in range(1, len(tracks)):
            current_pos = tracks[i]
            
            # 跳过变化太小的点，减少轨迹点数
            if abs(current_pos - last_pos) < 1.5:
                continue
                
            # 允许小幅回退，但防止大幅回退
            if current_pos >= last_pos or (last_pos - current_pos) < 3:
                cleaned_tracks.append(current_pos)
                last_pos = current_pos
            else:
                # 大幅回退时进行修正
                corrected_pos = last_pos + random.uniform(0.1, 1.0)
                cleaned_tracks.append(corrected_pos)
                last_pos = corrected_pos
        
        # 根据目标点数进行智能采样
        if target_points and len(cleaned_tracks) != target_points:
            if len(cleaned_tracks) > target_points:
                # 点数过多，进行下采样
                step = len(cleaned_tracks) / target_points
                optimized_tracks = [cleaned_tracks[0]]  # 保持起始点
                
                for i in range(1, target_points - 1):
                    idx = min(int(i * step), len(cleaned_tracks) - 1)
                    optimized_tracks.append(cleaned_tracks[idx])
                
                # 确保包含最后一个点
                if len(cleaned_tracks) > 1:
                    optimized_tracks.append(cleaned_tracks[-1])
                
                cleaned_tracks = optimized_tracks
            else:
                # 点数过少，进行插值上采样
                while len(cleaned_tracks) < target_points and len(cleaned_tracks) > 1:
                    new_tracks = [cleaned_tracks[0]]
                    
                    for i in range(len(cleaned_tracks) - 1):
                        new_tracks.append(cleaned_tracks[i])
                        # 在两点之间插入中点
                        if len(new_tracks) < target_points:
                            mid_point = (cleaned_tracks[i] + cleaned_tracks[i + 1]) / 2
                            mid_point += random.uniform(-0.5, 0.5)  # 添加小幅随机
                            new_tracks.append(mid_point)
                    
                    new_tracks.append(cleaned_tracks[-1])  # 最后一个点
                    cleaned_tracks = new_tracks
                    
                    if len(cleaned_tracks) >= target_points:
                        cleaned_tracks = cleaned_tracks[:target_points]
                        break
        elif not target_points and len(cleaned_tracks) > 200:
            # 默认情况：控制在200个点以内
            step = max(1, len(cleaned_tracks) // 150)
            optimized_tracks = []
            for i in range(0, len(cleaned_tracks), step):
                optimized_tracks.append(cleaned_tracks[i])
            if optimized_tracks[-1] != cleaned_tracks[-1]:
                optimized_tracks.append(cleaned_tracks[-1])
            cleaned_tracks = optimized_tracks
        
        return [int(x) for x in cleaned_tracks]

    def get_cookies(self, url, existing_cookies_str: str = None, cookie_id: str = "unknown"):
        """
        获取页面 cookies，增加重试机制
        :param url: 目标页面 URL
        :param existing_cookies_str: 现有的cookies字符串，用于设置到浏览器
        :param cookie_id: Cookie ID，用于日志记录
        :return: cookies 字符串或 None
        """
        try:
            # 设置cookie_id用于日志记录
            self.cookie_id = cookie_id

            # 记录验证开始时间
            verification_start_time = time.time()
            # 如果提供了现有cookies，先设置到浏览器
            if existing_cookies_str:
                logger.info("设置现有cookies到浏览器")
                self.set_cookies_from_string(existing_cookies_str)

            for attempt in range(self.max_retries):
                try:
                    # 启动网络监听（如果支持）
                    listen_started = False
                    try:
                        self.page.listen.start('slide')  # 监听包含 'slide' 的请求
                        listen_started = True
                    except Exception as e:
                        logger.warning(f"无法启动网络监听: {e}")

                    # 只在第一次或需要刷新时打开页面
                    if attempt == 0:
                        logger.info("首次打开页面")
                        self.page.get(url)  # 打开页面
                        time.sleep(random.uniform(1, 3))  # 随机等待，避免被检测

                        # 在页面加载后注入鼠标轨迹可视化
                        if self.show_mouse_trace:
                            logger.info("页面加载完成，重新注入鼠标轨迹可视化...")
                            self._inject_mouse_trace_visualization()
                    elif hasattr(self, 'Refresh') and self.Refresh:
                        logger.info("根据策略刷新页面")
                        self.page.refresh()
                        time.sleep(random.uniform(2, 4))
                        self.Refresh = False  # 重置刷新标志

                        # 页面刷新后重新注入鼠标轨迹可视化
                        if self.show_mouse_trace:
                            logger.info("页面刷新完成，重新注入鼠标轨迹可视化...")
                            self._inject_mouse_trace_visualization()
                    else:
                        logger.info("不刷新页面，点击重试按钮")
                        # 尝试点击重试按钮
                        try:
                            # 查找重试按钮
                            retry_button = None
                            retry_selectors = [
                                "#nc_1_refresh1",  # 主要重试按钮ID
                                "#nc_1_refresh2",  # 图标ID
                                ".errloading",     # 错误提示框class
                                "x://div[contains(@class,'errloading')]",  # xpath查找错误提示框
                                "x://div[contains(text(),'验证失败')]",      # 包含文本的div
                                ".nc_iconfont.icon_warn"  # 警告图标class
                            ]

                            for selector in retry_selectors:
                                try:
                                    retry_button = self.page.ele(selector, timeout=2)
                                    if retry_button:
                                        logger.info(f"找到重试按钮: {selector}")
                                        break
                                except:
                                    continue

                            if retry_button:
                                # 模拟人类点击行为
                                retry_button.hover()
                                time.sleep(random.uniform(0.2, 0.5))
                                retry_button.click()
                                logger.info("成功点击重试按钮")
                                time.sleep(random.uniform(1, 2))  # 等待重新加载验证码
                            else:
                                logger.warning("未找到重试按钮，等待后直接重试")
                                time.sleep(random.uniform(1, 2))

                        except Exception as retry_error:
                            logger.warning(f"点击重试按钮失败: {retry_error}")
                            time.sleep(random.uniform(0.5, 1.5))

                    # 在滑动前强制重新注入轨迹可视化
                    if self.show_mouse_trace:
                        logger.info("滑动前强制重新注入鼠标轨迹可视化...")
                        self._inject_mouse_trace_visualization()

                        # 等待一下确保注入完成
                        time.sleep(0.5)

                    self.country_gate_blocked = False
                    self._slide()  # 处理滑块验证码
                    if self.country_gate_blocked:
                        logger.warning("国家门控阻止订单详情滑块，本轮不再重试")
                        return None

                    if not self._detect_captcha():
                        logger.info("滑块验证成功，开始获取 cookies")

                        # 方法1: 尝试从监听数据获取新的cookies
                        new_cookies_from_response = None
                        if listen_started:
                            try:
                                # 使用正确的方式获取监听到的请求
                                packet_count = 0
                                for packet in self.page.listen.steps(count=10):  # 最多检查10个数据包
                                    packet_count += 1
                                    if 'slide' in packet.url:
                                        # 获取响应头
                                        try:
                                            response_headers = packet.response.headers
                                            # 尝试多种可能的Set-Cookie头名称
                                            set_cookie = (response_headers.get('Set-Cookie') or
                                                        response_headers.get('set-cookie') or
                                                        response_headers.get('SET-COOKIE'))

                                            if set_cookie:
                                                logger.info(f"从响应头获取到新的cookies")
                                                new_cookies_from_response = set_cookie
                                                break
                                        except Exception as header_e:
                                            logger.warning(f"获取响应头失败: {header_e}")

                                    # 如果没有更多数据包，跳出循环
                                    if packet_count >= 10:
                                        break

                            except Exception as e:
                                logger.warning(f"从监听数据获取 cookies 失败: {e}")

                        # 方法2: 直接从浏览器获取当前所有cookies
                        current_cookies_str = self.get_cookies_string()

                        # 优先返回从响应头获取的新cookies，否则返回浏览器当前cookies
                        result_cookies = new_cookies_from_response or current_cookies_str

                        if result_cookies:
                            logger.info("滑块验证成功，获取到cookies")

                            # 记录滑块验证成功到日志文件
                            verification_duration = time.time() - verification_start_time
                            log_captcha_event(self.cookie_id, "滑块验证成功", True,
                                f"耗时: {verification_duration:.2f}秒, 滑动次数: {self.slide_attempt}, cookies长度: {len(result_cookies)}")

                            if listen_started:
                                self.page.listen.stop()
                            self.close()
                            return result_cookies
                        else:
                            logger.warning("滑块验证成功但未获取到有效cookies")

                            # 记录滑块验证成功但cookies无效的情况
                            verification_duration = time.time() - verification_start_time
                            log_captcha_event(self.cookie_id, "滑块验证失败", False,
                                f"耗时: {verification_duration:.2f}秒, 滑动次数: {self.slide_attempt}, 原因: 验证成功但未获取到有效cookies")
                    else:
                        logger.warning(f"第 {attempt + 1} 次滑动验证失败，页面标题: {self.page.title}")

                        # 记录单次滑动验证失败
                        if attempt == self.max_retries - 1:  # 最后一次尝试失败时记录
                            verification_duration = time.time() - verification_start_time
                            log_captcha_event(self.cookie_id, "滑块验证失败", False,
                                f"耗时: {verification_duration:.2f}秒, 滑动次数: {self.slide_attempt}, 原因: 所有{self.max_retries}次尝试都失败")

                        # 清理监听，准备下次重试
                        if attempt < self.max_retries - 1:
                            logger.info(f"准备第 {attempt + 2} 次重试...")
                            if listen_started:
                                self.page.listen.stop()
                                listen_started = False

                except Exception as e:
                    logger.error(f"获取 Cookies 失败（第 {attempt + 1} 次）: {e}")

                    # 记录滑块验证异常
                    if attempt == self.max_retries - 1:  # 最后一次尝试异常时记录
                        verification_duration = time.time() - verification_start_time
                        log_captcha_event(self.cookie_id, "滑块验证异常", False,
                            f"耗时: {verification_duration:.2f}秒, 滑动次数: {getattr(self, 'slide_attempt', 0)}, 异常: {str(e)[:100]}")

                    # 确保清理监听
                    try:
                        self.page.listen.stop()
                    except:
                        pass

            logger.error("超过最大重试次数，获取 cookies 失败")

            # 记录最终失败
            verification_duration = time.time() - verification_start_time
            log_captcha_event(self.cookie_id, "滑块验证最终失败", False,
                f"耗时: {verification_duration:.2f}秒, 滑动次数: {getattr(self, 'slide_attempt', 0)}, 原因: 超过最大重试次数({self.max_retries})")

            return None

        finally:
            # 确保浏览器被关闭
            try:
                self.close()
            except:
                pass


    def _calculate_slide_distance(self):
        """
        动态计算滑动距离，适应不同分辨率
        """
        try:
            # 获取页面尺寸
            page_width = self.page.size[0] if hasattr(self.page, 'size') else 1920
            page_height = self.page.size[1] if hasattr(self.page, 'size') else 1080

            logger.info(f"检测到页面尺寸: {page_width}x{page_height}")

            # 尝试获取滑块轨道的实际宽度
            try:
                # 查找滑块轨道元素
                track_selectors = [
                    "#nc_1__scale_text",  # 滑块轨道
                    ".nc-lang-cnt",       # 验证码容器
                    "#nc_1_wrapper",      # 外层容器
                    ".nc_wrapper"         # 通用容器
                ]

                track_width = None
                for selector in track_selectors:
                    try:
                        track_element = self.page.ele(selector, timeout=2)
                        if track_element:
                            track_rect = track_element.rect
                            if track_rect and track_rect.width > 0:
                                track_width = track_rect.width
                                logger.info(f"找到轨道元素 {selector}，宽度: {track_width}px")
                                break
                    except:
                        continue

                if track_width:
                    # 基于实际轨道宽度计算滑动距离
                    # 通常需要滑动轨道宽度的70%-90%
                    slide_ratio = random.uniform(0.70, 0.90)
                    calculated_distance = int(track_width * slide_ratio)

                    # 添加小幅随机变化
                    distance_variation = random.randint(-20, 20)
                    final_distance = calculated_distance + distance_variation

                    # 确保距离在合理范围内
                    final_distance = max(200, min(600, final_distance))

                    logger.info(f"基于轨道宽度计算: {track_width}px * {slide_ratio:.2f} = {calculated_distance}px, 最终距离: {final_distance}px")
                    return final_distance

            except Exception as track_e:
                logger.warning(f"获取轨道宽度失败: {track_e}")

            # 备用方案：基于页面宽度估算
            if page_width <= 1366:  # 小屏幕
                base_distance = random.randint(250, 320)
                logger.info(f"小屏幕模式 ({page_width}px): 使用距离 {base_distance}px")
            elif page_width <= 1920:  # 中等屏幕
                base_distance = random.randint(300, 400)
                logger.info(f"中等屏幕模式 ({page_width}px): 使用距离 {base_distance}px")
            else:  # 大屏幕
                base_distance = random.randint(350, 480)
                logger.info(f"大屏幕模式 ({page_width}px): 使用距离 {base_distance}px")

            return base_distance

        except Exception as e:
            logger.warning(f"动态距离计算失败: {e}，使用默认距离")
            # 默认距离
            return 300 + random.randint(1, 100)

    def _inject_mouse_trace_visualization(self):
        """注入鼠标轨迹可视化代码"""
        try:
            logger.info("注入鼠标轨迹可视化代码...")

            # CSS样式 - 更醒目的设计
            css_code = """
            <style>
            .mouse-trace {
                position: fixed;
                width: 12px;
                height: 12px;
                background: rgba(255, 0, 0, 0.9);
                border: 2px solid rgba(255, 255, 255, 0.8);
                border-radius: 50%;
                pointer-events: none;
                z-index: 99999;
                transition: opacity 0.8s ease-out;
                box-shadow: 0 0 10px rgba(255, 0, 0, 0.5);
            }
            .mouse-trace.fade {
                opacity: 0;
            }
            .mouse-cursor {
                position: fixed;
                width: 20px;
                height: 20px;
                background: rgba(0, 255, 0, 0.9);
                border: 3px solid rgba(255, 255, 255, 0.9);
                border-radius: 50%;
                pointer-events: none;
                z-index: 100000;
                transform: translate(-50%, -50%);
                box-shadow: 0 0 15px rgba(0, 255, 0, 0.7);
                animation: pulse 1s infinite;
            }
            @keyframes pulse {
                0% { transform: translate(-50%, -50%) scale(1); }
                50% { transform: translate(-50%, -50%) scale(1.1); }
                100% { transform: translate(-50%, -50%) scale(1); }
            }

            </style>
            """

            # JavaScript代码
            js_code = """
            // 创建鼠标轨迹可视化
            window.mouseTracePoints = [];
            window.slideInfo = null;
            window.traceStatus = null;

            // 静默状态提示 - 不显示遮挡页面的元素
            function createStatusIndicator() {
                // 静默模式，不创建状态提示
                console.log('🖱️ 鼠标轨迹可视化已启用（静默模式）');
            }

            // 静默信息面板 - 不显示遮挡页面的元素
            function createInfoPanel() {
                // 静默模式，不创建信息面板
                window.slideInfo = null;
            }

            // 静默更新信息
            function updateInfo(text) {
                // 静默模式，不显示信息面板
                // console.log('轨迹信息:', text);  // 可选：输出到控制台用于调试
            }

            // 创建鼠标轨迹点
            function createTracePoint(x, y) {
                const point = document.createElement('div');
                point.className = 'mouse-trace';
                point.style.left = x + 'px';
                point.style.top = y + 'px';
                document.body.appendChild(point);

                window.mouseTracePoints.push(point);

                // 限制轨迹点数量
                if (window.mouseTracePoints.length > 100) {
                    const oldPoint = window.mouseTracePoints.shift();
                    if (oldPoint && oldPoint.parentNode) {
                        oldPoint.parentNode.removeChild(oldPoint);
                    }
                }

                // 设置淡出效果
                setTimeout(() => {
                    point.classList.add('fade');
                    setTimeout(() => {
                        if (point && point.parentNode) {
                            point.parentNode.removeChild(point);
                        }
                    }, 500);
                }, 1000);
            }

            // 创建鼠标光标指示器
            function createMouseCursor() {
                if (document.querySelector('.mouse-cursor')) return;
                const cursor = document.createElement('div');
                cursor.className = 'mouse-cursor';
                document.body.appendChild(cursor);
                return cursor;
            }

            // 监听鼠标移动
            let lastX = 0, lastY = 0;
            let moveCount = 0;
            let startTime = null;

            document.addEventListener('mousemove', function(e) {
                const cursor = document.querySelector('.mouse-cursor') || createMouseCursor();
                cursor.style.left = e.clientX + 'px';
                cursor.style.top = e.clientY + 'px';

                // 记录轨迹点 - 降低阈值，显示更多轨迹点
                if (Math.abs(e.clientX - lastX) > 1 || Math.abs(e.clientY - lastY) > 1) {
                    createTracePoint(e.clientX, e.clientY);
                    lastX = e.clientX;
                    lastY = e.clientY;
                    moveCount++;

                    if (!startTime) startTime = Date.now();

                    const elapsed = (Date.now() - startTime) / 1000;
                    updateInfo(`🖱️ 鼠标轨迹可视化<br>📊 移动次数: ${moveCount}<br>⏱️ 经过时间: ${elapsed.toFixed(1)}s<br>📍 当前位置: (${e.clientX}, ${e.clientY})<br>🔴 轨迹点: ${window.mouseTracePoints.length}`);
                }
            });

            // 监听鼠标按下和释放
            document.addEventListener('mousedown', function(e) {
                updateInfo(`鼠标轨迹可视化<br>鼠标按下: (${e.clientX}, ${e.clientY})<br>开始滑动...`);
                startTime = Date.now();
                moveCount = 0;
            });

            document.addEventListener('mouseup', function(e) {
                const elapsed = startTime ? (Date.now() - startTime) / 1000 : 0;
                updateInfo(`鼠标轨迹可视化<br>鼠标释放: (${e.clientX}, ${e.clientY})<br>滑动完成<br>总时间: ${elapsed.toFixed(2)}s<br>总移动: ${moveCount}次`);
            });

            // 静默测试按钮 - 不显示遮挡页面的元素
            function createTestButton() {
                // 静默模式，不创建测试按钮
                console.log('🖱️ 测试按钮已禁用（静默模式）');
            }

            // 初始化
            createInfoPanel();
            createMouseCursor();
            createStatusIndicator();
            createTestButton();

            // 静默模式控制台输出
            console.log('🖱️ 鼠标轨迹可视化已启用（静默模式）- 仅显示轨迹点和光标');
            """

            # 安全注入CSS - 等待DOM准备好
            css_inject_js = f"""
            (function() {{
                function injectCSS() {{
                    if (!document.head) {{
                        // 如果head不存在，创建一个
                        if (!document.documentElement) {{
                            return false;
                        }}
                        const head = document.createElement('head');
                        document.documentElement.appendChild(head);
                    }}

                    // 检查是否已经注入过CSS
                    if (document.querySelector('style[data-mouse-trace-css]')) {{
                        return true;
                    }}

                    const style = document.createElement('style');
                    style.setAttribute('data-mouse-trace-css', 'true');
                    style.innerHTML = `{css_code.replace('<style>', '').replace('</style>', '')}`;
                    document.head.appendChild(style);
                    return true;
                }}

                // 如果DOM还没准备好，等待
                if (document.readyState === 'loading') {{
                    document.addEventListener('DOMContentLoaded', injectCSS);
                }} else {{
                    injectCSS();
                }}
            }})();
            """

            self.page.run_js(css_inject_js)

            # 等待一下确保CSS注入完成
            time.sleep(0.2)

            # 安全注入JavaScript
            safe_js_code = f"""
            (function() {{
                // 确保DOM和body存在
                if (!document.body) {{
                    setTimeout(arguments.callee, 100);
                    return;
                }}

                {js_code}
            }})();
            """

            self.page.run_js(safe_js_code)

            logger.info("鼠标轨迹可视化代码注入成功")

        except Exception as e:
            logger.warning(f"注入鼠标轨迹可视化失败: {e}")

    def _javascript_maximize(self):
        """使用JavaScript尝试最大化窗口"""
        try:
            js_code = """
            // 尝试多种JavaScript最大化方法
            try {
                // 方法1: 移动窗口到左上角并调整大小
                window.moveTo(0, 0);
                window.resizeTo(screen.availWidth, screen.availHeight);

                // 方法2: 使用现代API
                if (window.screen && window.screen.availWidth) {
                    window.resizeTo(window.screen.availWidth, window.screen.availHeight);
                }

                // 方法3: 设置窗口外观尺寸
                if (window.outerWidth && window.outerHeight) {
                    var maxWidth = screen.availWidth || 1920;
                    var maxHeight = screen.availHeight || 1080;
                    window.resizeTo(maxWidth, maxHeight);
                    window.moveTo(0, 0);
                }

                console.log('JavaScript窗口最大化尝试完成');
                console.log('当前窗口尺寸:', window.outerWidth + 'x' + window.outerHeight);
                console.log('屏幕可用尺寸:', screen.availWidth + 'x' + screen.availHeight);

            } catch (e) {
                console.log('JavaScript最大化失败:', e);
            }
            """

            self.page.run_js(js_code)
            logger.debug("JavaScript最大化代码执行完成")

        except Exception as e:
            logger.debug(f"JavaScript最大化执行失败: {e}")

    def _force_maximize_windows(self):
        """使用Windows API强制最大化浏览器窗口"""
        try:
            import platform
            if platform.system() != "Windows":
                return

            # 尝试导入Windows API
            try:
                import win32gui
                import win32con

                # 查找Chrome浏览器窗口
                def enum_windows_callback(hwnd, windows):
                    if win32gui.IsWindowVisible(hwnd):
                        window_title = win32gui.GetWindowText(hwnd)
                        class_name = win32gui.GetClassName(hwnd)

                        # 查找Chrome窗口
                        if ("Chrome" in class_name or "chrome" in window_title.lower() or
                            "Google Chrome" in window_title or "Chromium" in window_title):
                            windows.append(hwnd)
                    return True

                windows = []
                win32gui.EnumWindows(enum_windows_callback, windows)

                if windows:
                    # 最大化最新的Chrome窗口
                    hwnd = windows[-1]
                    logger.info(f"找到Chrome窗口，正在强制最大化...")

                    # 显示窗口并最大化
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # 先恢复
                    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)  # 再最大化

                    # 将窗口置于前台
                    win32gui.SetForegroundWindow(hwnd)

                    logger.info("Windows API强制最大化完成")
                else:
                    logger.debug("未找到Chrome窗口")

            except ImportError:
                logger.debug("pywin32未安装，跳过Windows API最大化")
            except Exception as api_e:
                logger.debug(f"Windows API操作失败: {api_e}")

        except Exception as e:
            logger.debug(f"强制最大化失败: {e}")

    def _detect_captcha(self):
        """检测页面是否被拦截"""
        return self.page.title == "验证码拦截"

    def adjust_y_offset_settings(self, y_drift_range=None, shake_range=None,
                                fast_move_multiplier=None, directional_range=None, max_y_offset=None):
        """
        调整垂直偏移量设置

        :param y_drift_range: 整体漂移趋势范围 ±像素（默认3）
        :param shake_range: 基础抖动范围 ±像素（默认1.5）
        :param fast_move_multiplier: 快速移动时的抖动放大倍数（默认1.8）
        :param directional_range: 方向性偏移范围（默认1.0）
        :param max_y_offset: 最大垂直偏移限制 ±像素（默认8）
        """
        if y_drift_range is not None:
            self.y_drift_range = y_drift_range
            logger.info(f"整体漂移趋势范围调整为: ±{y_drift_range}像素")

        if shake_range is not None:
            self.shake_range = shake_range
            logger.info(f"基础抖动范围调整为: ±{shake_range}像素")

        if fast_move_multiplier is not None:
            self.fast_move_multiplier = fast_move_multiplier
            logger.info(f"快速移动抖动放大倍数调整为: {fast_move_multiplier}")

        if directional_range is not None:
            self.directional_range = directional_range
            logger.info(f"方向性偏移范围调整为: {directional_range}")

        if max_y_offset is not None:
            self.max_y_offset = max_y_offset
            logger.info(f"最大垂直偏移限制调整为: ±{max_y_offset}像素")

        logger.info("垂直偏移量设置已更新")

    def close(self):
        """关闭浏览器"""
        # logger.info("关闭浏览器")
        self.browser.quit()


class XianyuApis:
    def __init__(self):
        self.url = 'https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/'
        self.session = requests.Session()
        self.session.headers.update({
            'accept': 'application/json',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.goofish.com',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://www.goofish.com/',
            'sec-ch-ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        })
        
    def clear_duplicate_cookies(self):
        """清理重复的cookies"""
        # 创建一个新的CookieJar
        new_jar = requests.cookies.RequestsCookieJar()
        
        # 记录已经添加过的cookie名称
        added_cookies = set()
        
        # 按照cookies列表的逆序遍历（最新的通常在后面）
        cookie_list = list(self.session.cookies)
        cookie_list.reverse()
        
        for cookie in cookie_list:
            # 如果这个cookie名称还没有添加过，就添加到新jar中
            if cookie.name not in added_cookies:
                new_jar.set_cookie(cookie)
                added_cookies.add(cookie.name)
                
        # 替换session的cookies
        self.session.cookies = new_jar
        
    def hasLogin(self, retry_count=0):
        """调用hasLogin.do接口进行登录状态检查"""
        if retry_count >= 2:
            logger.error("Login检查失败，重试次数过多")
            return False
            
        try:
            url = 'https://passport.goofish.com/newlogin/silentHasLogin.do'
            params = {
                'documentReferer':"https%3A%2F%2Fwww.goofish.com%2F",
                'appEntrance':'xianyu_sdkSilent',
                'appName': 'xianyu',
                'fromSite': '0',
                'ltl':'true'
            }
            data = {
                'hid': self.session.cookies.get('unb', ''),
                'ltl': 'true',
                'appName': 'xianyu',
                'appEntrance': 'web',
                '_csrf_token': self.session.cookies.get('XSRF-TOKEN', ''),
                'umidToken': '',
                'hsiz': self.session.cookies.get('cookie2', ''),
                'mainPage': 'false',
                'isMobile': 'false',
                'lang': 'zh_CN',
                'returnUrl': '',
                'fromSite': '77',
                'isIframe': 'true',
                'documentReferer': 'https://www.goofish.com/',
                'defaultView': 'hasLogin',
                'umidTag': 'SERVER',
                'deviceId': self.session.cookies.get('cna', '')
            }
            
            response = self.session.post(url, params=params, data=data)
            res_json = response.json()
            if res_json.get('content', {}).get('success'):
                logger.debug("Login成功")
                # 清理和更新cookies
                self.clear_duplicate_cookies()
                return True
            else:
                logger.warning(f"Login失败: {res_json}")
                time.sleep(0.5)
                return self.hasLogin(retry_count + 1)
                
        except Exception as e:
            logger.error(f"Login请求异常: {str(e)}")
            time.sleep(0.5)
            return self.hasLogin(retry_count + 1)

    def get_token(self, device_id, retry_count=0):
        if retry_count >= 2:  # 最多重试3次
            logger.warning("获取token失败，尝试重新登陆")
            # 尝试通过hasLogin重新登录
            if self.hasLogin():
                logger.info("重新登录成功，重新尝试获取token")
                return self.get_token(device_id, 0)  # 重置重试次数
            else:
                logger.error("重新登录失败，Cookie已失效")
                logger.error("🔴 程序即将退出，请更新.env文件中的COOKIES_STR后重新启动")
                return False# sys.exit(1)  # 直接退出程序
            
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idlemessage.pc.login.token',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        data_val = '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"' + device_id + '"}'
        data = {
            'data': data_val,
        }
        
        # 简单获取token，信任cookies已清理干净
        token = self.session.cookies.get('_m_h5_tk', '').split('_')[0]
        
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign
        
        try:
            response = self.session.post('https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/', params=params, data=data)
            res_json = response.json()
            
            if isinstance(res_json, dict):
                ret_value = res_json.get('ret', [])
                # 检查ret是否包含成功信息
                if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                    logger.warning(f"Token API调用失败，错误信息: {ret_value}")
                    # 处理响应中的Set-Cookie
                    if ret_value[0] == 'FAIL_SYS_USER_VALIDATE':
                        url = res_json["data"]["url"]
                        drission = DrissionHandler(
                            is_headless=False,
                            maximize_window=True,  # 启用窗口最大化
                            show_mouse_trace=True  # 启用鼠标轨迹
                        )
                        cookies = drission.get_cookies(url)
                        if cookies:
                            new_x5sec = trans_cookies(cookies)
                            self.session.cookies.set("x5sec",new_x5sec["x5sec"])
                    if 'Set-Cookie' in response.headers:
                        # logger.debug("检测到Set-Cookie，更新cookie")  # 降级为DEBUG并简化
                        self.clear_duplicate_cookies()
                    time.sleep(0.5)
                    return self.get_token(device_id, retry_count + 1)
                else:
                    logger.info("Token获取成功")
                    return res_json
            else:
                logger.error(f"Token API返回格式异常: {res_json}")
                return self.get_token(device_id, retry_count + 1)
                
        except Exception as e:
            logger.error(f"Token API请求异常: {str(e)}")
            time.sleep(0.5)
            return self.get_token(device_id, retry_count + 1)

    def get_item_info(self, item_id, retry_count=0):
        """获取商品信息，自动处理token失效的情况"""
        if retry_count >= 3:  # 最多重试3次
            logger.error("获取商品信息失败，重试次数过多")
            return {"error": "获取商品信息失败，重试次数过多"}
            
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idle.pc.detail',
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
        }
        
        data_val = '{"itemId":"' + item_id + '"}'
        data = {
            'data': data_val,
        }
        
        # 简单获取token，信任cookies已清理干净
        token = self.session.cookies.get('_m_h5_tk', '').split('_')[0]
        
        sign = generate_sign(params['t'], token, data_val)
        params['sign'] = sign
        
        try:
            response = self.session.post(
                'https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/', 
                params=params, 
                data=data
            )
            
            res_json = response.json()
            # 检查返回状态
            if isinstance(res_json, dict):
                ret_value = res_json.get('ret', [])
                # 检查ret是否包含成功信息
                if not any('SUCCESS::调用成功' in ret for ret in ret_value):
                    logger.warning(f"商品信息API调用失败，错误信息: {ret_value}")
                    # 处理响应中的Set-Cookie
                    if 'Set-Cookie' in response.headers:
                        logger.debug("检测到Set-Cookie，更新cookie")
                        self.clear_duplicate_cookies()
                    time.sleep(0.5)
                    return self.get_item_info(item_id, retry_count + 1)
                else:
                    logger.debug(f"商品信息获取成功: {item_id}")
                    return res_json
            else:
                logger.error(f"商品信息API返回格式异常: {res_json}")
                return self.get_item_info(item_id, retry_count + 1)
                
        except Exception as e:
            logger.error(f"商品信息API请求异常: {str(e)}")
            time.sleep(0.5)
            return self.get_item_info(item_id, retry_count + 1)
        
class XianyuLive:
     def __init__(self, cookies_str):
        self.xianyu = XianyuApis()
        self.base_url = 'wss://wss-goofish.dingtalk.com/'
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.xianyu.session.cookies.update(self.cookies)  # 直接使用 session.cookies.update
        self.myid = self.cookies['unb']
        self.device_id = generate_device_id(self.myid)
    
     async def refresh_token(self):
         """刷新token"""
         try:
             logger.info("开始刷新token...")
             
             # 获取新token（如果Cookie失效，get_token会直接退出程序）
             token_result = self.xianyu.get_token(self.device_id)
             if 'data' in token_result and 'accessToken' in token_result['data']:
                 new_token = token_result['data']['accessToken']
                 self.current_token = new_token
                 self.last_token_refresh_time = time.time()
                 logger.info("Token刷新成功")
                 return new_token
             else:
                 logger.error(f"Token刷新失败: {token_result}")
                 return None
                 
         except Exception as e:
             logger.error(f"Token刷新异常: {str(e)}")
             return None
     

def trans_cookies(cookies_str: str) -> Dict[str, str]:
    """解析cookie字符串为字典"""
    cookies = {}
    for cookie in cookies_str.split("; "):
        try:
            parts = cookie.split('=', 1)
            if len(parts) == 2:
                cookies[parts[0]] = parts[1]
        except:
            continue
    return cookies


def generate_mid() -> str:
    """生成mid"""
    import random
    random_part = int(1000 * random.random())
    timestamp = int(time.time() * 1000)
    return f"{random_part}{timestamp} 0"


def generate_uuid() -> str:
    """生成uuid"""
    timestamp = int(time.time() * 1000)
    return f"-{timestamp}1"


def generate_device_id(user_id: str) -> str:
    """生成设备ID"""
    import random
    
    # 字符集
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    result = []
    
    for i in range(36):
        if i in [8, 13, 18, 23]:
            result.append("-")
        elif i == 14:
            result.append("4")
        else:
            if i == 19:
                # 对于位置19，需要特殊处理
                rand_val = int(16 * random.random())
                result.append(chars[(rand_val & 0x3) | 0x8])
            else:
                rand_val = int(16 * random.random())
                result.append(chars[rand_val])
    
    return ''.join(result) + "-" + user_id


def generate_sign(t: str, token: str, data: str) -> str:
    """生成签名"""
    app_key = "34839810"
    msg = f"{token}&{t}&{app_key}&{data}"
    
    # 使用MD5生成签名
    md5_hash = hashlib.md5()
    md5_hash.update(msg.encode('utf-8'))
    return md5_hash.hexdigest()



async def refresh_token(cookies_str: str):
    new = XianyuLive(cookies_str)
    token = await new.refresh_token()
    cookie_list = list(new.xianyu.session.cookies)
    cookie_list.reverse()
    res_Cookie = {}
    for cookie in cookie_list:
        value = cookie.value.split(';')[0]
        res_Cookie[cookie.name] = value.strip()
    return token,res_Cookie

# 测试代码已注释，避免与运行中的事件循环冲突
# ck = 'cna=2x0VIEthuBgCAQFBy07P5aax; t=92064eac9aab68795e909c84b6666cd4; tracknick=xy771982658888; unb=2219383264998; havana_lgc2_77=eyJoaWQiOjIyMTkzODMyNjQ5OTgsInNnIjoiYjk0M2ExZGM5NmRmYjQzMjE3M2ZiOGY4OGU3MTAxNjAiLCJzaXRlIjo3NywidG9rZW4iOiIxb3IzSnhCTEZXR2p1RUtvUjJIanJpUSJ9; _hvn_lgc_=77; havana_lgc_exp=1756475265341; isg=BFtbbqoIt2xEAssYNMFYqNXj6r_FMG8y1w9dDU2YIdpxLHsO1RbTgimvwoSiDMcq; cookie2=195d0616914d49cdc0f3814853a178f5; mtop_partitioned_detect=1; _m_h5_tk=cc382a584eaa9c3199f16b836d65261b_1754570160098; _m_h5_tk_enc=601c990574902ba494c5a64d56c323fc; _samesite_flag_=true; sgcookie=E100SV3cXtpyaSTASi3UJw1CozDY5JER%2FtZyBK%2FGpP70RTciu6SlJpnymRbPfhlmD%2F4lwLWX%2B1i9MA6JoFYX82tG%2FFjvLA3r4Jo9TFBttkpNkSFR6Hji6h2zGCJBo6%2BgXrK9; csg=bf0b371b; _tb_token_=34333886818b6; sdkSilent=1754483979355; x5sec=7b22733b32223a2231306464633338373739333435393936222c22617365727665723b33223a22307c43494353794d5147454d446c2b4e4c362f2f2f2f2f774561447a49794d546b7a4f444d794e6a51354f5467374e4369414244443271636d4c41773d3d227d; tfstk=gBHndvwmpXPBuhWCt32BODnGdSOtOJw7T4B8y8Uy_Pz1JwBK4a0o7qndpYnzr40T54VWAzUzr4nr9hpvHDiQF8ukkKpvyneHa4qFU9ow4oZlahFU81Acc88vkdCObWTLU4EmqsPZbPZu4azEzGya2PrUY9uz75r4DwyzU4-gQoqhzzPzz57amPzzU8urbhq77kyzU4owjuGW4TzmUAMwr4TgRGwn7Aq3trow1TWoxtFUuDzGUNDgx94qYPXPEyKbDMoiveXjXJMmoo3pQ90m4mGzsYbwozMibvlrfwxancctMWcM8T4tpPNxL-jyLc23-SkgggXxucmrM5D90Kw3Lyl8d0IDJcDnJDMi2g5aKJhgiv22hwz-6mDuqYTWIqmrcbPiEwjzz152s367b3HNN_NUfl4YFvklQOvVV0KMjsOQTlZpkhxGN_NUfl4vjhffPWr_vEC..; x5secdata=xg83ae4e91aa7f9ddajaec50940bbd5e854a6fb0457cb4876a021754575403a-717315356a829576438abaac3en2219383264998a0__bx__h5api.m.goofish.com:443/h5/mtop.taobao.idlemessage.pc.login.token/1.0; x5sectag=122733'
# print(asyncio.run(refresh_token(ck)))
