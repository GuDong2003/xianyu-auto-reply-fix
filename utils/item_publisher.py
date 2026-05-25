import io
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from PIL import Image
from loguru import logger

from utils.xianyu_utils import generate_sign, trans_cookies


class ItemPublisher:
    """闲鱼商品发布服务。"""

    APP_KEY = "34839810"
    BASE_REFERER = "https://www.goofish.com/"
    BASE_ORIGIN = "https://www.goofish.com"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    ALLOWED_DELIVERY_CHOICES = {"包邮", "按距离计费", "一口价", "无需邮寄"}

    def __init__(self, cookies_str: str, cookie_id: str = "", proxy_config: Optional[Dict[str, Any]] = None):
        cleaned_cookies = str(cookies_str or "").strip()
        if not cleaned_cookies:
            raise ValueError("Cookie 为空，无法发布商品")

        self.cookie_id = str(cookie_id or "unknown").strip() or "unknown"
        self.cookies = trans_cookies(cleaned_cookies)
        self.cookies_str = self._serialize_cookies(self.cookies)
        self.session: Optional[aiohttp.ClientSession] = None
        self.proxy_config = dict(proxy_config or {})
        self._http_proxy_url = self._build_http_proxy_url()

    async def __aenter__(self):
        await self.create_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()

    async def create_session(self):
        if self.session:
            return

        connector = None
        if self._is_socks_proxy():
            try:
                from aiohttp_socks import ProxyConnector, ProxyType

                connector = ProxyConnector(
                    proxy_type=ProxyType.SOCKS5,
                    host=self.proxy_config.get("proxy_host"),
                    port=int(self.proxy_config.get("proxy_port") or 0),
                    username=self.proxy_config.get("proxy_user") or None,
                    password=self.proxy_config.get("proxy_pass") or None,
                    rdns=True,
                )
            except ImportError:
                logger.error(f"【{self.cookie_id}】商品发布使用 SOCKS5 代理需要安装 aiohttp-socks")

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=40),
            connector=connector,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8,zh-TW;q=0.7,ja;q=0.6",
            },
        )

        if self._http_proxy_url:
            logger.info(f"【{self.cookie_id}】商品发布服务启用账号代理: {self._masked_proxy_url()}")

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    def _build_http_proxy_url(self) -> Optional[str]:
        proxy_type = str(self.proxy_config.get("proxy_type") or "none").strip().lower()
        proxy_host = str(self.proxy_config.get("proxy_host") or "").strip()
        proxy_port = self.proxy_config.get("proxy_port") or 0
        if proxy_type in {"", "none"} or not proxy_host or not proxy_port:
            return None
        if proxy_type not in {"http", "https", "socks5"}:
            return None

        proxy_user = str(self.proxy_config.get("proxy_user") or "").strip()
        proxy_pass = str(self.proxy_config.get("proxy_pass") or "").strip()
        auth = ""
        if proxy_user:
            auth = proxy_user
            if proxy_pass:
                auth += f":{proxy_pass}"
            auth += "@"
        return f"{proxy_type}://{auth}{proxy_host}:{proxy_port}"

    def _masked_proxy_url(self) -> str:
        proxy_type = str(self.proxy_config.get("proxy_type") or "none").strip().lower()
        proxy_host = str(self.proxy_config.get("proxy_host") or "").strip()
        proxy_port = self.proxy_config.get("proxy_port") or 0
        if proxy_type in {"", "none"} or not proxy_host or not proxy_port:
            return "none"
        return f"{proxy_type}://{proxy_host}:{proxy_port}"

    def _is_socks_proxy(self) -> bool:
        return str(self.proxy_config.get("proxy_type") or "").strip().lower() == "socks5"

    def _request_kwargs(self) -> Dict[str, Any]:
        if not self._http_proxy_url or self._is_socks_proxy():
            return {}
        return {"proxy": self._http_proxy_url}

    @staticmethod
    def is_success_response(payload: Dict[str, Any]) -> bool:
        ret_values = payload.get("ret") if isinstance(payload, dict) else None
        if not isinstance(ret_values, list):
            return False
        return any(str(item).startswith("SUCCESS::") for item in ret_values)

    @staticmethod
    def extract_error_message(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return "响应格式异常"

        ret_values = payload.get("ret")
        if isinstance(ret_values, list) and ret_values:
            return str(ret_values[0])

        error_fields = (
            payload.get("message"),
            payload.get("errorMsg"),
            payload.get("msg"),
        )
        for value in error_fields:
            if value:
                return str(value)

        return "请求失败"

    @classmethod
    def extract_published_item_id(cls, payload: Dict[str, Any]) -> Optional[str]:
        data = payload.get("data") if isinstance(payload, dict) else None
        return cls._search_item_id(data)

    @classmethod
    def _search_item_id(cls, node: Any) -> Optional[str]:
        if isinstance(node, dict):
            for key in ("itemId", "item_id", "idleItemId", "idleId"):
                value = node.get(key)
                normalized = cls._normalize_candidate_id(value)
                if normalized:
                    return normalized

            for value in node.values():
                found = cls._search_item_id(value)
                if found:
                    return found

        if isinstance(node, list):
            for item in node:
                found = cls._search_item_id(item)
                if found:
                    return found

        return None

    @staticmethod
    def _normalize_candidate_id(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if text and text.isdigit() and len(text) >= 6:
            return text
        return None

    async def publish_item(
        self,
        *,
        title: str,
        description: str,
        images: List[Dict[str, Any]],
        current_price: Optional[float],
        original_price: Optional[float],
        delivery_choice: str,
        post_price: Optional[float],
        can_self_pickup: bool,
    ) -> Dict[str, Any]:
        if delivery_choice not in self.ALLOWED_DELIVERY_CHOICES:
            raise ValueError("不支持的运费方式")

        if delivery_choice == "一口价" and (post_price is None or post_price < 0):
            raise ValueError("运费方式为一口价时，邮费必须大于等于 0")

        uploaded_images = []
        for image in images:
            uploaded_images.append(
                await self.upload_image(
                    image_bytes=image["content"],
                    filename=image.get("filename") or "item.jpg",
                )
            )

        publish_title = str(title or "").strip()
        publish_desc = str(description or title or "").strip()
        if not publish_title:
            raise ValueError("商品标题不能为空")
        if not publish_desc:
            raise ValueError("商品描述不能为空")

        channel_res = await self.get_public_channel(publish_title, uploaded_images)
        if not self.is_success_response(channel_res):
            raise RuntimeError(f"获取发布类目失败: {self.extract_error_message(channel_res)}")

        location = await self.get_default_location()

        publish_data = self._build_publish_payload(
            title=publish_title,
            description=publish_desc,
            uploaded_images=uploaded_images,
            channel_res=channel_res,
            location=location,
            current_price=current_price,
            original_price=original_price,
            delivery_choice=delivery_choice,
            post_price=post_price,
            can_self_pickup=can_self_pickup,
        )

        publish_res = await self._post_mtop(
            api_name="mtop.idle.pc.idleitem.publish",
            version="1.0",
            payload=publish_data,
            spm_cnt="a21ybx.publish.0.0",
            spm_pre="a21ybx.home.sidebar.1.46413da6EPl7v5",
        )

        publish_res["_uploaded_images"] = uploaded_images
        return publish_res

    async def upload_image(self, *, image_bytes: bytes, filename: str) -> Dict[str, Any]:
        if not image_bytes:
            raise ValueError("图片内容为空")

        await self.create_session()

        normalized_bytes, width, height = self._normalize_image(image_bytes)
        safe_filename = self._normalize_filename(filename)

        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            normalized_bytes,
            filename=safe_filename,
            content_type="image/jpeg",
        )

        headers = self._build_headers()
        headers.pop("content-type", None)
        headers["accept"] = "*/*"
        params = {
            "floderId": "0",
            "appkey": "xy_chat",
            "_input_charset": "utf-8",
        }

        async with self.session.post(
            "https://stream-upload.goofish.com/api/upload.api",
            params=params,
            data=form_data,
            headers=headers,
            **self._request_kwargs(),
        ) as response:
            self._update_cookies_from_response(response)
            response_text = await response.text()

        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"图片上传返回异常响应: {response_text[:200]}") from exc

        image_object = payload.get("object") or payload.get("data") or payload.get("result") or {}
        image_url = image_object.get("url") or payload.get("url")
        if not image_url:
            raise RuntimeError(f"图片上传失败: {self.extract_error_message(payload)}")

        pixel_text = image_object.get("pix") or ""
        if isinstance(pixel_text, str) and "x" in pixel_text:
            try:
                width, height = [int(part) for part in pixel_text.lower().split("x", 1)]
            except (TypeError, ValueError):
                pass

        return {
            "url": image_url,
            "width": width,
            "height": height,
        }

    async def get_public_channel(self, title: str, images_info: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {
            "title": title,
            "lockCpv": False,
            "multiSKU": False,
            "publishScene": "mainPublish",
            "scene": "newPublishChoice",
            "description": title,
            "imageInfos": [
                {
                    "extraInfo": {
                        "isH": "false",
                        "isT": "false",
                        "raw": "false",
                    },
                    "isQrCode": False,
                    "url": image["url"],
                    "heightSize": image["height"],
                    "widthSize": image["width"],
                    "major": True,
                    "type": 0,
                    "status": "done",
                }
                for image in images_info
            ],
            "uniqueCode": self._build_unique_code(),
        }

        return await self._post_mtop(
            api_name="mtop.taobao.idle.kgraph.property.recommend",
            version="2.0",
            payload=payload,
            spm_cnt="a21ybx.publish.0.0",
            spm_pre="a21ybx.item.sidebar.1.67321598K9Vgx8",
        )

    async def get_default_location(self) -> Dict[str, Any]:
        payload = {
            "longitude": 118.78248347393424,
            "latitude": 31.91629189813543,
        }
        result = await self._post_mtop(
            api_name="mtop.taobao.idle.local.poi.get",
            version="1.0",
            payload=payload,
            spm_cnt="a21ybx.publish.0.0",
            spm_pre="a21ybx.item.sidebar.1.38262218ame5nr",
            extra_headers={
                "eagleeye-userdata": "spm-cnt=a21ybx",
            },
        )

        if not self.is_success_response(result):
            raise RuntimeError(f"获取默认地址失败: {self.extract_error_message(result)}")

        address_list = (
            result.get("data", {}).get("commonAddresses")
            if isinstance(result.get("data"), dict)
            else None
        ) or []
        if not address_list:
            raise RuntimeError("未获取到账号默认地址")
        return address_list[0]

    def _build_publish_payload(
        self,
        *,
        title: str,
        description: str,
        uploaded_images: List[Dict[str, Any]],
        channel_res: Dict[str, Any],
        location: Dict[str, Any],
        current_price: Optional[float],
        original_price: Optional[float],
        delivery_choice: str,
        post_price: Optional[float],
        can_self_pickup: bool,
    ) -> Dict[str, Any]:
        category_result = channel_res.get("data", {}).get("categoryPredictResult", {})
        card_list = channel_res.get("data", {}).get("cardList", []) or []

        payload = {
            "freebies": False,
            "itemTypeStr": "b",
            "quantity": "1",
            "simpleItem": "true",
            "imageInfoDOList": [
                {
                    "extraInfo": {
                        "isH": "false",
                        "isT": "false",
                        "raw": "false",
                    },
                    "isQrCode": False,
                    "url": image["url"],
                    "heightSize": image["height"],
                    "widthSize": image["width"],
                    "major": True,
                    "type": 0,
                    "status": "done",
                }
                for image in uploaded_images
            ],
            "itemTextDTO": {
                "desc": description,
                "title": title,
                "titleDescSeparate": description != title,
            },
            "itemLabelExtList": self._build_item_label_list(card_list),
            "itemPriceDTO": {},
            "userRightsProtocols": [
                {
                    "enable": False,
                    "serviceCode": "SKILL_PLAY_NO_MIND",
                }
            ],
            "itemPostFeeDTO": {
                "canFreeShipping": False,
                "supportFreight": False,
                "onlyTakeSelf": False,
            },
            "itemAddrDTO": {
                "area": location.get("area", ""),
                "city": location.get("city", ""),
                "divisionId": location.get("divisionId", 0),
                "gps": f"{location.get('longitude')},{location.get('latitude')}",
                "poiId": location.get("poiId", ""),
                "poiName": location.get("poi", ""),
                "prov": location.get("prov", ""),
            },
            "defaultPrice": False,
            "itemCatDTO": {
                "catId": str(category_result.get("catId", "")),
                "catName": str(category_result.get("catName", "")),
                "channelCatId": str(category_result.get("channelCatId", "")),
                "tbCatId": str(category_result.get("tbCatId", "")),
            },
            "uniqueCode": self._build_unique_code(),
            "sourceId": "pcMainPublish",
            "bizcode": "pcMainPublish",
            "publishScene": "pcMainPublish",
        }

        self._apply_delivery_settings(
            payload=payload,
            delivery_choice=delivery_choice,
            post_price=post_price,
            can_self_pickup=can_self_pickup,
        )
        self._apply_price_settings(
            payload=payload,
            current_price=current_price,
            original_price=original_price,
        )

        return payload

    def _apply_delivery_settings(
        self,
        *,
        payload: Dict[str, Any],
        delivery_choice: str,
        post_price: Optional[float],
        can_self_pickup: bool,
    ):
        post_fee = payload["itemPostFeeDTO"]

        if delivery_choice == "包邮":
            post_fee["canFreeShipping"] = True
            post_fee["supportFreight"] = True
        elif delivery_choice == "按距离计费":
            post_fee["supportFreight"] = True
            post_fee["templateId"] = "-100"
        elif delivery_choice == "一口价":
            post_fee["supportFreight"] = True
            post_fee["postPriceInCent"] = str(int(round((post_price or 0) * 100)))
            post_fee["templateId"] = "0"
        elif delivery_choice == "无需邮寄":
            post_fee["templateId"] = "0"

        if can_self_pickup:
            post_fee["onlyTakeSelf"] = True
            payload["onlyTakeSelf"] = True

    @staticmethod
    def _apply_price_settings(
        *,
        payload: Dict[str, Any],
        current_price: Optional[float],
        original_price: Optional[float],
    ):
        price_dto = payload["itemPriceDTO"]
        has_price = False

        if current_price is not None and current_price > 0:
            price_dto["priceInCent"] = str(int(round(current_price * 100)))
            has_price = True

        if original_price is not None and original_price > 0:
            price_dto["origPriceInCent"] = str(int(round(original_price * 100)))
            has_price = True

        if not has_price:
            payload["defaultPrice"] = True

    @staticmethod
    def _build_item_label_list(card_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        labels: List[Dict[str, Any]] = []
        for card in card_list:
            card_data = card.get("cardData") or {}
            values_list = card_data.get("valuesList") or []
            for value in values_list:
                if not value.get("isClicked"):
                    continue
                labels.append(
                    {
                        "channelCateName": value.get("catName"),
                        "valueId": None,
                        "channelCateId": value.get("channelCatId"),
                        "valueName": None,
                        "tbCatId": value.get("tbCatId"),
                        "subPropertyId": None,
                        "labelType": "common",
                        "subValueId": None,
                        "labelId": None,
                        "propertyName": card_data.get("propertyName"),
                        "isUserClick": "1",
                        "isUserCancel": None,
                        "from": "newPublishChoice",
                        "propertyId": card_data.get("propertyId"),
                        "labelFrom": "newPublish",
                        "text": value.get("catName"),
                        "properties": (
                            f"{card_data.get('propertyId')}##{card_data.get('propertyName')}:"
                            f"{value.get('channelCatId')}##{value.get('catName')}"
                        ),
                    }
                )
                break
        return labels

    async def _post_mtop(
        self,
        *,
        api_name: str,
        version: str,
        payload: Dict[str, Any],
        spm_cnt: str,
        spm_pre: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        await self.create_session()

        data_val = json.dumps(payload, separators=(",", ":"))
        params = {
            "jsv": "2.7.2",
            "appKey": self.APP_KEY,
            "t": str(int(time.time() * 1000)),
            "sign": "",
            "v": version,
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api_name,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": spm_cnt,
            "spm_pre": spm_pre,
            "log_id": self._build_log_id(),
        }

        token = self._get_token()
        params["sign"] = generate_sign(params["t"], token, data_val)
        headers = self._build_headers(extra_headers=extra_headers)
        url = f"https://h5api.m.goofish.com/h5/{api_name}/{version}/"

        async with self.session.post(
            url,
            params=params,
            data={"data": data_val},
            headers=headers,
            **self._request_kwargs(),
        ) as response:
            self._update_cookies_from_response(response)
            response_text = await response.text()

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{api_name} 返回非 JSON 响应: {response_text[:200]}") from exc

    def _build_headers(self, *, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "accept": "application/json",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded",
            "origin": self.BASE_ORIGIN,
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": self.BASE_REFERER,
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": self.USER_AGENT,
            "cookie": self.cookies_str,
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _get_token(self) -> str:
        token_cookie = self.cookies.get("_m_h5_tk", "")
        token = str(token_cookie).split("_", 1)[0].strip()
        if not token:
            raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法生成签名")
        return token

    def _update_cookies_from_response(self, response: aiohttp.ClientResponse) -> bool:
        updated = False
        for cookie_name, morsel in response.cookies.items():
            cookie_value = morsel.value
            if not cookie_value:
                continue
            if self.cookies.get(cookie_name) == cookie_value:
                continue
            self.cookies[cookie_name] = cookie_value
            updated = True

        if updated:
            self.cookies_str = self._serialize_cookies(self.cookies)
            logger.info(f"【{self.cookie_id}】发布服务已更新响应 Cookie")

        return updated

    @staticmethod
    def _serialize_cookies(cookies: Dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in cookies.items() if str(key).strip())

    @staticmethod
    def _normalize_filename(filename: str) -> str:
        stem = os.path.splitext(os.path.basename(str(filename or "item")))[0] or "item"
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
        return f"{safe_stem}.jpg"

    @staticmethod
    def _build_unique_code() -> str:
        return str(int(time.time() * 1000000))

    @staticmethod
    def _build_log_id() -> str:
        return f"publish{int(time.time() * 1000)}"

    @staticmethod
    def _normalize_image(image_bytes: bytes) -> Tuple[bytes, int, int]:
        max_dimension = 2048
        image = Image.open(io.BytesIO(image_bytes))

        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            if image.mode == "P":
                image = image.convert("RGBA")
            background.paste(image, mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        width, height = image.size
        if width > max_dimension or height > max_dimension:
            ratio = min(max_dimension / width, max_dimension / height)
            width = max(1, int(width * ratio))
            height = max(1, int(height * ratio))
            image = image.resize((width, height), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        return output.getvalue(), width, height
