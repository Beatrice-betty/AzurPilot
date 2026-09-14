"""高级材质的「透明 / 磨砂」自定义滑块。

只作用于高级材质两个主题（``advanced_material`` / ``dark_advanced_material``）：
普通主题的卡片本来就是不透明纯色，没有可调的材质层。

设计要点
--------
* **不阻塞**。PyWebIO 的输入控件全在 :mod:`pywebio.input`，而那是阻塞式的，
  在「背景」页这种作用域渲染里用不了。所以滑块用原生 ``<input type=range>``
  自己画，事件在浏览器端处理，不往返服务端 —— 拖动时才能跟手。
* **写用户层令牌**。滑块不改主题自己的令牌，只写
  ``--alas-card-user-bg`` / ``--alas-card-user-blur`` / ``--alas-tile-user-*``
  / ``--alas-log-user-*`` 这几个「用户层」变量。它们在 CSS 里以
  ``var(用户值, var(主题值))`` 兜底：
    - 没拖过滑块 → 退回主题原值，外观与加滑块前完全一致；
    - 拖了滑块   → ``:root`` 上的内联变量立即覆盖，无需重载样式表。
* **底色分量从主题令牌里解析**。不硬编码白/深色 —— 直接读各令牌的 rgb
  分量，只替换 alpha，这样高级白与高级黑共用同一段逻辑。
* **刻度以主题默认值居中**（:data:`DEFAULT_POS`）：
  0 → 完全透明 / 不磨砂，50 → 主题默认那一档，100 → 默认值的两倍。
  默认位置左右都有余量，能往「更透」也能往「更实」调。
* **持久化**。值存 ``localStorage``，脚本随部件挂载时立即应用。

分组
----
卡片：左栏五张（调度器 / 统计&打开 / 运行中 / 队列中 / 等待中）
      + dashboard（总览）+ resource_chart（资源统计）
贴片：右栏滚动栏 —— 统计板块（含四个子板块）+ 滚动日志栏

两组令牌完全脱钩，对应「总览页滚动栏贴片用自己的令牌，其他卡片用共用的令牌」。
"""
from __future__ import annotations

import json

from module.webui.lang import t

# 与主题 CSS 的默认值一一对应，改 CSS 时这几个数要跟着改
#   cardAlpha      → --alas-apple-card-bg
#   cardBlur       → --alas-card-blur        blur(8px)
#   cardSaturate   → --alas-card-blur        saturate(160% / 150%)
#   tileAlpha      → --alas-tile-user-bg-outer（容器外侧，取卡片色）
#   tileInnerAlpha → --alas-stat-panel-bg    （统计子板块）
#   tileBlur       → --alas-card-blur        （贴片容器沿用卡片磨砂）
#   logAlpha       → --alas-log-column-bg
#   logBlur        → --alas-log-column-blur  blur(7px) / blur(6px)
DEFAULTS = {
    'advanced_material': {
        'cardAlpha': 0.28,
        'cardBlur': 8,
        'cardSaturate': 160,
        'tileAlpha': 0.28,
        'tileInnerAlpha': 0.18,
        'logAlpha': 0.22,
        'logBlur': 7,
    },
    'dark_advanced_material': {
        'cardAlpha': 0.45,
        'cardBlur': 8,
        'cardSaturate': 150,
        'tileAlpha': 0.45,
        'tileInnerAlpha': 0.28,
        'logAlpha': 0.42,
        'logBlur': 6,
    },
}

# 滑块刻度 0..100：0 = 透明，50 = 主题默认，100 = 默认的两倍
DEFAULT_POS = 50
ALPHA_RANGE = 2.0
BLUR_RANGE = 2.0

STORAGE_KEY = 'alas.ui.material'
_SCOPE = 'alas-material-sliders'


def theme_defaults(theme: str | None) -> dict:
    """该主题的 CSS 默认值；非高级材质返回空 dict（调用方据此跳过渲染）。"""
    if theme in DEFAULTS:
        return dict(DEFAULTS[theme])
    return {}


def build_html(theme: str | None) -> str:
    """滑块的静态标记。主题不匹配时返回空串。"""
    if not theme_defaults(theme):
        return ''

    def row(key: str, text: str) -> str:
        return f"""
      <div class="alas-ms-row">
        <span class="alas-ms-label">{text}</span>
        <input class="alas-ms-range" type="range" min="0" max="100" step="1"
               value="{DEFAULT_POS}" data-key="{key}" aria-label="{text}">
        <output class="alas-ms-out" data-out="{key}"></output>
        <button class="alas-ms-reset" type="button" data-reset="{key}">{t('Gui.Stat.MaterialReset')}</button>
      </div>"""

    return f"""
<style>
#{_SCOPE} {{
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid var(--alas-apple-glass-border, rgba(255,255,255,.18));
  background: var(--alas-apple-card-bg, rgba(255,255,255,.20));
  backdrop-filter: var(--alas-card-blur, blur(8px));
  -webkit-backdrop-filter: var(--alas-card-blur, blur(8px));
  color: inherit;
  font-size: 13px;
}}
#{_SCOPE} .alas-ms-title {{ font-size: 14px; font-weight: 600; margin: 0 0 4px; }}
#{_SCOPE} .alas-ms-note {{ opacity: .62; font-size: 12px; margin: 0 0 10px; }}
#{_SCOPE} .alas-ms-group {{ font-weight: 600; opacity: .82; margin: 10px 0 2px; }}
#{_SCOPE} .alas-ms-row {{
  display: grid;
  grid-template-columns: 3.4em 1fr 5.4em 3.2em;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
}}
#{_SCOPE} .alas-ms-label {{ opacity: .85; }}
#{_SCOPE} .alas-ms-range {{ width: 100%; accent-color: #3b82f6; cursor: pointer; }}
#{_SCOPE} .alas-ms-out {{
  text-align: right; opacity: .72; font-variant-numeric: tabular-nums;
}}
#{_SCOPE} .alas-ms-reset {{
  padding: 2px 8px;
  border-radius: 7px;
  border: 1px solid var(--alas-apple-glass-border, rgba(255,255,255,.22));
  background: transparent;
  color: inherit;
  font-size: 12px;
  cursor: pointer;
}}
#{_SCOPE} .alas-ms-reset:hover {{ opacity: .72; }}
</style>
<div id="{_SCOPE}">
  <p class="alas-ms-title">{t('Gui.Stat.MaterialTitle')}</p>
  <p class="alas-ms-note">{t('Gui.Stat.MaterialAdvancedOnly')}</p>
  <div class="alas-ms-group">{t('Gui.Stat.MaterialCards')}</div>
  {row('cardAlpha', t('Gui.Stat.MaterialTransparency'))}
  {row('cardBlur', t('Gui.Stat.MaterialBlur'))}
  <div class="alas-ms-group">{t('Gui.Stat.MaterialTiles')}</div>
  {row('tileAlpha', t('Gui.Stat.MaterialTransparency'))}
  {row('tileBlur', t('Gui.Stat.MaterialBlur'))}
</div>"""


def build_js(theme: str | None) -> str:
    """滑块的浏览器端逻辑。主题不匹配时返回空串。"""
    defaults = theme_defaults(theme)
    if not defaults:
        return ''

    cfg = json.dumps({
        'scope': _SCOPE,
        'key': STORAGE_KEY,
        'defaultPos': DEFAULT_POS,
        'alphaRange': ALPHA_RANGE,
        'blurRange': BLUR_RANGE,
        'defaults': defaults,
    }, ensure_ascii=False)

    return f"""
(function () {{
  var CFG = {cfg};
  var root = document.getElementById(CFG.scope);
  if (!root) {{ return; }}
  var docEl = document.documentElement;
  var D = CFG.defaults;

  // 只改 alpha 不改色相：从主题令牌取底色分量再拼 rgba
  function rgbOf(token, fallback) {{
    var v = getComputedStyle(docEl).getPropertyValue(token).trim();
    var m = v.match(/rgba?\\(\\s*(\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)/);
    return m ? (m[1] + ',' + m[2] + ',' + m[3]) : fallback;
  }}
  var RGB = {{
    card: rgbOf('--alas-apple-card-bg', '255,255,255'),
    tile: rgbOf('--alas-stat-panel-bg', '255,255,255'),
    log: rgbOf('--alas-log-column-bg', '255,255,255')
  }};

  function scale(v) {{ return v / CFG.defaultPos; }}

  function alpha(base, v) {{
    return Math.min(1, base * scale(v) * (CFG.alphaRange / 2)).toFixed(4);
  }}
  function blurOf(px, v) {{
    var r = px * scale(v) * (CFG.blurRange / 2);
    if (r <= 0.01) {{ return 'none'; }}
    return 'blur(' + r.toFixed(2) + 'px) saturate(' + D.cardSaturate + '%)';
  }}

  // 贴片的「透明」同时管容器外侧/统计子板块/日志栏，各自按同一倍率缩放
  var WRITE = {{
    cardAlpha: function (v) {{
      docEl.style.setProperty('--alas-card-user-bg',
        'rgba(' + RGB.card + ',' + alpha(D.cardAlpha, v) + ')');
    }},
    tileAlpha: function (v) {{
      docEl.style.setProperty('--alas-tile-user-bg-outer',
        'rgba(' + RGB.card + ',' + alpha(D.tileAlpha, v) + ')');
      docEl.style.setProperty('--alas-tile-user-bg',
        'rgba(' + RGB.tile + ',' + alpha(D.tileInnerAlpha, v) + ')');
      docEl.style.setProperty('--alas-log-user-bg',
        'rgba(' + RGB.log + ',' + alpha(D.logAlpha, v) + ')');
    }},
    cardBlur: function (v) {{
      docEl.style.setProperty('--alas-card-user-blur', blurOf(D.cardBlur, v));
    }},
    tileBlur: function (v) {{
      docEl.style.setProperty('--alas-tile-user-blur', blurOf(D.cardBlur, v));
      docEl.style.setProperty('--alas-log-user-blur', blurOf(D.logBlur, v));
    }}
  }};

  function apply(key, v) {{
    var fn = WRITE[key];
    if (fn) {{ fn(v); }}
  }}

  var USER_TOKENS = ['--alas-card-user-bg', '--alas-card-user-blur',
                     '--alas-tile-user-bg', '--alas-tile-user-bg-outer',
                     '--alas-tile-user-blur', '--alas-log-user-bg',
                     '--alas-log-user-blur'];

  function clearUserTier() {{
    USER_TOKENS.forEach(function (n) {{ docEl.style.removeProperty(n); }});
  }}

  function readStore() {{
    try {{ return JSON.parse(localStorage.getItem(CFG.key) || '{{}}') || {{}}; }}
    catch (e) {{ return {{}}; }}
  }}
  function writeStore(o) {{
    try {{ localStorage.setItem(CFG.key, JSON.stringify(o)); }} catch (e) {{}}
  }}

  var ranges = [].slice.call(root.querySelectorAll('[data-key]'));
  var outs = {{}};
  [].slice.call(root.querySelectorAll('[data-out]')).forEach(function (o) {{
    outs[o.getAttribute('data-out')] = o;
  }});

  // 读数用相对默认值的百分比，与刻度语义一致（50 档 → 100%）
  function label(key, v) {{
    var pct = Math.round(scale(v) * (CFG.alphaRange / 2) * 100);
    if (key.indexOf('Blur') >= 0) {{
      var px = D.cardBlur * scale(v) * (CFG.blurRange / 2);
      return pct + '% · ' + px.toFixed(1) + 'px';
    }}
    return pct + '%';
  }}

  // 先清用户层：某项重置后残留的内联值会继续生效
  function renderAll() {{
    var store = readStore();
    clearUserTier();
    ranges.forEach(function (r) {{
      var key = r.getAttribute('data-key');
      var v = (key in store) ? store[key] : CFG.defaultPos;
      r.value = v;
      if (outs[key]) {{ outs[key].textContent = label(key, v); }}
      if (key in store) {{ apply(key, v); }}
    }});
  }}

  root.addEventListener('input', function (ev) {{
    var r = ev.target;
    if (!r || !r.hasAttribute('data-key')) {{ return; }}
    var key = r.getAttribute('data-key');
    var v = Number(r.value);
    var store = readStore();
    store[key] = v;
    writeStore(store);
    if (outs[key]) {{ outs[key].textContent = label(key, v); }}
    apply(key, v);
  }});

  root.addEventListener('click', function (ev) {{
    var b = ev.target;
    if (!b || !b.hasAttribute('data-reset')) {{ return; }}
    var store = readStore();
    delete store[b.getAttribute('data-reset')];
    writeStore(store);
    renderAll();
  }});

  renderAll();
}})();
"""
