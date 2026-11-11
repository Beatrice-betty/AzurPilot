"""已安装的奖励模板和绑定在离线模式下可继续使用。"""

import base64
import hashlib
import json

import cv2
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from module.statistics.reward_templates import CachedRewardTemplates, decode_icon


@pytest.fixture
def installed_cache(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    image = np.random.default_rng(42).integers(0, 255, (49, 49, 3), np.uint8)
    png = cv2.imencode('.png', cv2.cvtColor(image, cv2.COLOR_RGB2BGR))[1].tobytes()
    digest = hashlib.sha256(png).hexdigest()
    entry = {'identity_id': 'U-original', 'template_id': 'Blueprint_2', 'kind': 'formal',
             'icon_sha256': hashlib.sha256(image.tobytes()).hexdigest(), 'sha256': digest}
    payload = json.dumps({'revision': 1, 'templates': [entry], 'replace_ids': [
        {'unknown_id': 'U-registered', 'item_id': 'Coins', 'tag': ''},
    ]}).encode()
    envelope = json.dumps({'payload_base64': base64.b64encode(payload).decode(),
                           'signature': base64.b64encode(key.sign(payload)).decode()}).encode()
    folder = tmp_path / 'catalog' / 'icons'
    folder.mkdir(parents=True)
    icon_path = folder / (digest + '.png')
    icon_path.write_bytes(png)
    active = folder.parent / 'active.json'
    active.write_text(json.dumps({'manifest': base64.b64encode(envelope).decode()}), encoding='utf-8')
    frame = np.zeros((96, 96, 3), np.uint8)
    frame[21:70, 40:89] = image
    return tmp_path, public, frame, active, icon_path


def test_cached_templates_and_registered_identities_work_without_downloads(installed_cache):
    folder, public, frame, active, icon_path = installed_cache
    previous = active.read_bytes(), icon_path.read_bytes()
    unknown = np.zeros_like(frame)
    source = 'U?' + hashlib.sha256(unknown[21:70, 40:89].tobytes()).hexdigest()
    (folder / 'bindings.json').write_text(json.dumps({
        source: {'identity_id': 'U-registered', 'template_id': 'U-registered'},
    }), encoding='utf-8')
    templates = CachedRewardTemplates(folder, public_key=public)

    assert templates.identify(frame, '1') == ('Blueprint_2', 'U-original')
    assert templates.identify(unknown, '2') == ('Coins', source)
    assert templates.identify(unknown, 'Oil') == ('Oil', 'Oil')
    assert (active.read_bytes(), icon_path.read_bytes()) == previous


@pytest.mark.parametrize('damaged', ['icon', 'manifest'])
def test_invalid_cached_template_falls_back_to_local_recognition(installed_cache, damaged):
    folder, public, frame, active, icon_path = installed_cache
    target = icon_path if damaged == 'icon' else active
    target.write_bytes(b'tampered')
    templates = CachedRewardTemplates(folder, public_key=public)

    assert templates.identify(frame, 'Oil') == ('Oil', 'Oil')
    source, original = templates.identify(frame, '1')
    assert source == original and source.startswith('U?')
    assert target.read_bytes() == b'tampered'


def test_non_8bit_png_is_rejected_before_matching():
    image = np.ones((49, 49, 3), dtype=np.uint16) * 1024
    png = cv2.imencode('.png', image)[1].tobytes()
    with pytest.raises(ValueError, match='template pixels'):
        decode_icon(png)
