"""本地掉落物识别，复用已经下载并验签的旧模板。"""

import base64
import hashlib
import json
import re
from functools import lru_cache

import cv2
import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from module.base.device_id import get_device_id
from module.logger import logger
from module.persistence.database import current_directory

TEMPLATE_PUBLIC_KEY = 'r1HA3vwbOo1v4cDtrU5yJNAKZXuvmW8ot6cXeBC9IN4='
ITEM_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
DEFAULT_THRESHOLDS = {'formal': 0.92, 'cluster': 0.96, 'margin': 0.03, 'phash_distance': 6, 'color': 30}


def unpack_signed(data, key):
    try:
        envelope = json.loads(data)
        payload = base64.b64decode(envelope['payload_base64'], validate=True)
        signature = base64.b64decode(envelope['signature'], validate=True)
        key.verify(signature, payload)
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError('Expected object')
        return result
    except (InvalidSignature, KeyError, ValueError, TypeError) as error:
        raise ValueError('Invalid signed template document') from error


def decode_icon(png):
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Template is not PNG')
    # Check dimensions before invoking the decoder, including malicious oversized IHDRs.
    if png[12:16] != b'IHDR' or len(png) < 33 or (
            int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')) != (49, 49):
        raise ValueError('Template must be 49 by 49 pixels')
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.dtype != np.uint8 or image.shape not in ((49, 49, 3), (49, 49, 4)):
        raise ValueError('Invalid template pixels')
    if image.shape[2] == 4 and np.any(image[:, :, 3] != 255):
        raise ValueError('Transparent template pixels are forbidden')
    return cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2RGB)


def icon_features(image):
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    coefficients = cv2.dct(cv2.resize(gray, (32, 32)).astype(np.float32))[:8, :8].ravel()
    bits = coefficients > np.median(coefficients[1:])
    bits[0] = False
    fingerprint = sum(int(bit) << index for index, bit in enumerate(bits))
    return fingerprint, image.mean((0, 1)), float(np.sqrt(image.var((0, 1)).mean()))


def summarize_items(items):
    """合并同名且标签相同的掉落，原始条目供历史统计使用。"""
    merged = {}
    for item in items:
        key = item['item'], item.get('tag') or ''
        merged[key] = merged.get(key, 0) + item['amount']
    return [{'item': item, 'tag': tag, 'amount': amount}
            for (item, tag), amount in sorted(merged.items())]


def local_drop_directory(instance):
    """沿用旧模板和委托诊断的目录归属。"""
    identity = hashlib.sha256(f'{get_device_id()}\0{instance}'.encode()).hexdigest()[:16]
    return current_directory() / 'cloud' / identity


@lru_cache(maxsize=16)
def cached_reward_templates(directory):
    return CachedRewardTemplates(directory)


class CachedRewardTemplates:
    """从本地目录读取已签名的模板和已登记的旧图标对应关系。"""

    def __init__(self, directory, *, public_key=TEMPLATE_PUBLIC_KEY):
        self.directory = directory
        self.entries = []
        self.mappings = {}
        self.bindings = {}
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        active = directory / 'catalog' / 'active.json'
        if active.is_file():
            try:
                key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
                state = json.loads(active.read_bytes())
                payload = unpack_signed(base64.b64decode(state['manifest'], validate=True), key)
                self._activate(payload)
            except (OSError, KeyError, TypeError, ValueError, cv2.error) as error:
                logger.warning('[掉落统计] 本地模板缓存不可读，使用内置模板：%s', type(error).__name__)

        bindings = directory / 'bindings.json'
        if bindings.is_file():
            try:
                values = json.loads(bindings.read_bytes())
                if not isinstance(values, dict):
                    raise ValueError('Invalid template bindings')
                self.bindings = {
                    source: row for source, row in values.items()
                    if isinstance(row, dict) and all(
                        isinstance(row.get(field), str) and ITEM_ID.fullmatch(row[field])
                        for field in ('identity_id', 'template_id')
                    )
                }
            except (OSError, ValueError) as error:
                logger.warning('[掉落统计] 无法读取旧图标对应关系：%s', type(error).__name__)

    def _validate(self, payload):
        revision = payload.get('revision')
        if type(revision) is not int or revision < 1:
            raise ValueError('Invalid template revision')
        entries, mappings = payload.get('templates'), payload.get('replace_ids')
        if not isinstance(entries, list) or len(entries) > 100000 or not isinstance(mappings, list):
            raise ValueError('Invalid template manifest')
        keys = set()
        for entry in entries:
            if (not isinstance(entry, dict) or not ITEM_ID.fullmatch(entry.get('template_id', ''))
                    or not ITEM_ID.fullmatch(entry.get('identity_id', ''))
                    or entry.get('kind') not in ('formal', 'temporary')
                    or not HASH.fullmatch(entry.get('sha256', ''))
                    or not HASH.fullmatch(entry.get('icon_sha256', ''))):
                raise ValueError('Invalid template entry')
            key = entry['identity_id'], entry['icon_sha256']
            if key in keys:
                raise ValueError('Duplicate template variant')
            keys.add(key)
        for row in mappings:
            if (not isinstance(row, dict) or not ITEM_ID.fullmatch(row.get('unknown_id', ''))
                    or not ITEM_ID.fullmatch(row.get('item_id', ''))
                    or not re.fullmatch(r'[A-Za-z0-9_-]{0,32}', row.get('tag', ''))):
                raise ValueError('Invalid replacement mapping')
        if len({row['unknown_id'] for row in mappings}) != len(mappings):
            raise ValueError('Duplicate replacement mapping')
        thresholds = {**DEFAULT_THRESHOLDS, **payload.get('match_thresholds', {})}
        for name, maximum in [('formal', 1), ('cluster', 1), ('margin', 1), ('color', 255), ('phash_distance', 64)]:
            value = thresholds[name]
            if type(value) not in (int, float) or not 0 <= value <= maximum:
                raise ValueError('Invalid template matching thresholds')

    def _activate(self, payload):
        self._validate(payload)
        entries = []
        for entry in payload['templates']:
            png = (self.directory / 'catalog' / 'icons' / (entry['sha256'] + '.png')).read_bytes()
            if hashlib.sha256(png).hexdigest() != entry['sha256']:
                raise ValueError('Template file checksum mismatch')
            image = decode_icon(png)
            if hashlib.sha256(image.tobytes()).hexdigest() != entry['icon_sha256']:
                raise ValueError('Template pixel checksum mismatch')
            entries.append((entry, image, *icon_features(image)))
        self.entries = entries
        self.mappings = {row['unknown_id']: row['item_id'] for row in payload['replace_ids']}
        self.thresholds.update(payload.get('match_thresholds', {}))

    def identify(self, image, local_name):
        """保留内置模板优先于临时图标的顺序，未知图标以本地哈希记账。"""
        icon = np.ascontiguousarray(image[21:70, 40:89, :3], dtype=np.uint8)
        if icon.shape != (49, 49, 3):
            raise ValueError('Expected a 96px reward item')
        for kind in ('formal', 'temporary'):
            entry = self.match(icon, kind=kind)
            if entry:
                return entry['template_id'], entry['identity_id']
            if kind == 'formal' and local_name and local_name != 'DefaultItem' and not local_name.isdigit():
                return local_name, local_name
        source = 'U?' + hashlib.sha256(icon.tobytes()).hexdigest()
        binding = self.bindings.get(source)
        if binding:
            return self.mappings.get(binding['identity_id'], binding['template_id']), source
        return source, source

    def match(self, image, *, kind):
        if not self.entries:
            return None
        scores = {}
        fingerprint, mean, stddev = icon_features(image)
        for entry, candidate, candidate_hash, candidate_mean, candidate_stddev in self.entries:
            if entry['kind'] != kind:
                continue
            if (fingerprint ^ candidate_hash).bit_count() > self.thresholds['phash_distance']:
                continue
            if np.max(np.abs(mean - candidate_mean)) > self.thresholds['color']:
                continue
            if min(stddev, candidate_stddev) < 5:
                score = 1 - float(np.sqrt(np.mean((image.astype(float) - candidate) ** 2))) / 255
            else:
                score = float(cv2.matchTemplate(image, candidate, cv2.TM_CCOEFF_NORMED)[0, 0])
            key = entry['template_id']
            if key not in scores or score > scores[key][0]:
                scores[key] = score, entry
        ranked = sorted(scores.values(), key=lambda row: row[0], reverse=True)
        if ranked and ranked[0][0] >= self.thresholds['formal' if kind == 'formal' else 'cluster']:
            if len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= self.thresholds['margin']:
                return ranked[0][1]
        return None
