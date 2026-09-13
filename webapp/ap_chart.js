(function () {
    function setTooltipContent(tipEl, rows) {
        if (!tipEl || !Array.isArray(rows)) return;
        while (tipEl.firstChild) {
            tipEl.removeChild(tipEl.firstChild);
        }
        rows.forEach(function (row) {
            if (!row) return;
            var div = document.createElement('div');
            if (row.style && typeof row.style === 'object') {
                Object.keys(row.style).forEach(function (k) {
                    div.style[k] = row.style[k];
                });
            }
            if (row.parts && Array.isArray(row.parts)) {
                row.parts.forEach(function (part) {
                    if (!part) return;
                    if (part.type === 'text') {
                        var span = document.createElement('span');
                        span.textContent = part.value != null ? String(part.value) : '';
                        if (part.style && typeof part.style === 'object') {
                            Object.keys(part.style).forEach(function (k) {
                                span.style[k] = part.style[k];
                            });
                        }
                        div.appendChild(span);
                    } else if (part.type === 'bold') {
                        var b = document.createElement('b');
                        b.textContent = part.value != null ? String(part.value) : '';
                        if (part.style && typeof part.style === 'object') {
                            Object.keys(part.style).forEach(function (k) {
                                b.style[k] = part.style[k];
                            });
                        }
                        div.appendChild(b);
                    }
                });
            }
            tipEl.appendChild(div);
        });
    }

    var chartType = "__CHART_TYPE__";
    var I18N = __I18N__;
    var labels = __LABELS__;
    var opens = __OPENS__;
    var highs = __HIGHS__;
    var lows = __LOWS__;
    var closes = __CLOSES__;
    var counts = __COUNTS__;
    var ap = __AP__;
    var apTs = __AP_TS__;
    var avg = __AVG__;
    var isDetailMode = __IS_DETAIL_MODE__;
    var sources = __SOURCES__;
    var yellowCoins = __YELLOW_COINS__;
    var purpleCoins = __PURPLE_COINS__;
    var coinsSources = __COINS_SOURCES__;
    var showCoins = __SHOW_COINS__;
    var lineAsset = __ASSET__;
    var lineAssetTs = __ASSET_TS__;
    var hasAssetSeries = lineAsset && lineAsset.length > 0;
    var lineDistance = __DISTANCE__;
    var hasDistanceSeries = lineDistance && lineDistance.length > 0;

    var seriesVisible = [true, true, true, true, true];
    var _isSelecting = false;
    // 系列色唯一来源是 CSS 令牌 --ap-series-*；曲线/数值/图例/提示框共用同一组值
    var __FALLBACK_SERIES = {
        1: '#64b5f6', 2: '#ffd54f', 3: '#ce93d8', 4: '#81c784', 5: '#1565c0',
        ma5: '#ffeb3b', ma10: '#e91e63', avg: '#ff9800'
    };
    function __apSeriesColor(key) {
        var cs = getComputedStyle(document.documentElement);
        var v = (cs.getPropertyValue('--ap-series-' + key) || '').trim();
        return v || __FALLBACK_SERIES[key] || '#888';
    }
    /* 把当前描边色转成「同色系加深」的描边色。
       light 底：压暗到 40% 并半透明，形成一圈深色勾边；
       dark 底：提亮，形成一圈浅色勾边；
       glow === 'none' 时返回透明，配合 __apGlowPad = 0 完全关闭。 */
    function __apEdgeColor(color, glow) {
        if (glow === 'none' || !color) { return 'rgba(0, 0, 0, 0)'; }
        var m = String(color).match(/rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        if (!m) { return glow === 'light' ? 'rgba(0,0,0,0.5)' : 'rgba(255,255,255,0.5)'; }
        var f = (glow === 'light') ? 0.40 : 1.55;
        var r = Math.min(255, Math.round(+m[1] * f));
        var g = Math.min(255, Math.round(+m[2] * f));
        var b = Math.min(255, Math.round(+m[3] * f));
        return 'rgba(' + r + ',' + g + ',' + b + ',0.7)';
    }

    var seriesColors = [__apSeriesColor(1), __apSeriesColor(3),
                        __apSeriesColor(2), __apSeriesColor(4),
                        __apSeriesColor(5)];
    var seriesNames = [I18N.ChartSeriesAp, I18N.ChartSeriesPurple, I18N.ChartSeriesYellow, I18N.ChartSeriesAsset, I18N.ChartSeriesDistance];

    var chartId = "__CHART_ID__";
    window.__apChartCleanups = window.__apChartCleanups || {};
    if (window.__apChartCleanups[chartId]) {
        window.__apChartCleanups[chartId]();
    }

    var nn = chartType === 'line' ? ap.length : labels.length;
    if (nn < 1) return;

    var cv = document.getElementById(chartId);
    if (!cv) return;
    var tipEl = document.getElementById(chartId + "_tip");
    var ovCv = document.getElementById(chartId + "_ov");

    var dpr = window.devicePixelRatio || 1;
    var W, H, pad, gW, gH;
    var cleanupHandlers = [];
    var animationFrameId = null;

    function cleanup() {
        cleanupHandlers.forEach(function (item) {
            if (item.type === "__disconnect") { item.target.disconnect(); return; }
            item.target.removeEventListener(item.type, item.handler, item.options);
        });
        cleanupHandlers = [];
        if (animationFrameId !== null) cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
        if (window.__apChartCleanups[chartId] === cleanup) {
            delete window.__apChartCleanups[chartId];
        }
    }

    // 函数声明可提升，initChart 之外的 resizeCanvas() 也能调用
    function setCanvasTransform(c) {
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function addListener(target, type, handler, options) {
        if (!target) return;
        target.addEventListener(type, handler, options);
        cleanupHandlers.push({ target: target, type: type, handler: handler, options: options });
    }

    window.__apChartCleanups[chartId] = cleanup;

    watchThemeRedraw();

    // 容器尺寸变化后必须重新同步位图，否则重绘即非等比拉伸（重影）
    var __resizeTimer = null;
    function handleCanvasResize() {
        if (__resizeTimer) { clearTimeout(__resizeTimer); }
        __resizeTimer = setTimeout(function () {
            __resizeTimer = null;
            initChart();
        }, 120);
    }
    if (typeof ResizeObserver === "function") {
        var __ro = new ResizeObserver(handleCanvasResize);
        __ro.observe(cv.parentElement || cv);
        cleanupHandlers.push({
            target: __ro, type: "__disconnect", handler: null, options: null });
    }
    addListener(window, "resize", handleCanvasResize);


    // 延迟渲染以确保 canvas 布局完成，避免首次加载坐标偏移
    animationFrameId = requestAnimationFrame(function () {
        animationFrameId = null;
        initChart();
    });


    // 位图尺寸按 dpr 匹配显示尺寸。不要写 cv.style.width/height：canvas 是
    // width:100% 自适应，把量到的像素写回内联样式后会自我强化（宽度由自己
    // 上一步的值决定），容器变窄时报旧宽度、位图永不更新且无法恢复
    function resizeCanvas() {

        // 宽度取 getBoundingClientRect()：clientWidth 会四舍五入掉小数，差 1px 即被拉伸
        var rect = cv.getBoundingClientRect();
        var w = Math.round(rect.width);
        var h = Math.round(cv.clientHeight);
        if (!w) { w = Math.round(cv.clientWidth); }
        if (!h) { h = 360; }
        if (!w || !h) { return null; }
        W = w;
        H = h;
        var bw = Math.round(w * dpr);
        var bh = Math.round(h * dpr);
        if (cv.width !== bw || cv.height !== bh) {
            cv.width = bw;
            cv.height = bh;
        }
        if (ovCv.width !== bw || ovCv.height !== bh) {
            ovCv.width = bw;
            ovCv.height = bh;
        }
        var c2 = cv.getContext("2d");
        var o2 = ovCv.getContext("2d");
        setCanvasTransform(c2);
        o2.setTransform(1, 0, 0, 1, 0, 0);
        return { w: w, h: h };
    }


    // canvas 的 fillStyle 不接受 var(--x)，必须读计算值
    var __FALLBACK_CC = { bg: '#1a1a2e', grid: '#2a2a3e', label: '#666',
                     up: '#ff6b6b', down: '#2ee6c5', lw: 'dark', glow: 'dark' };

    // 不用 shadowBlur：蒙一层阴影会让细线发糊，K 线影线尤其明显
    var __apLineW = 1;

    // 描边函数必须定义在 initChart 内：模块作用域取不到局部 ctx 会抛 ReferenceError
    var __apGlowPad = 0;
    var __apGlowMode = 'light';

    var _cc = __FALLBACK_CC;

    function chartThemeColors() {
        var cs = getComputedStyle(document.documentElement);
        function pick(n) { return (cs.getPropertyValue(n) || '').trim(); }
        var isInline = !!(cv && cv.closest
            && cv.closest('#pywebio-scope-stat_panels'));
        return {
            // 两条路径脱钩：右栏滚动栏(isInline) 全透明，透出磨砂玻璃；
            // 二级菜单统计页自涂不透明底色（那里 canvas 直接铺在页面上，
            // 没有玻璃垫着，全透明会让网格线和刻度看不清）
            bg: isInline ? 'rgba(0, 0, 0, 0)'
                : (pick('--alas-chart-bitmap-bg')
                   || pick('--alas-chart-bg')
                   || pick('--ap-chart-container-bg')
                   || __FALLBACK_CC.bg),
            grid: pick('--alas-chart-grid') || __FALLBACK_CC.grid,
            label: pick('--alas-chart-label') || __FALLBACK_CC.label,
            up: pick('--alas-chart-up') || __FALLBACK_CC.up,
            down: pick('--alas-chart-down') || __FALLBACK_CC.down,
            lw: (cs.getPropertyValue('--alas-chart-lw') || '').trim()
                || __FALLBACK_CC.lw,
            // 'light' 底用暗描边、'dark' 底用亮描边；none 关闭
            glow: pick('--alas-chart-glow') || __FALLBACK_CC.glow
        };
    }

    // 主题切换靠 <head> 里样式表增删，位图里烘焙的是旧主题颜色，必须重绘
    var __themeWatch = null;
    function watchThemeRedraw() {
        if (__themeWatch || typeof MutationObserver !== 'function') { return; }
        var pending = null;
        __themeWatch = new MutationObserver(function () {
            if (pending) { clearTimeout(pending); }
            pending = setTimeout(function () {
                pending = null;
                initChart();
            }, 80);
        });
        __themeWatch.observe(document.head, { childList: true });
        cleanupHandlers.push({
            target: __themeWatch, type: '__disconnect', handler: null, options: null });
    }

    function initChart() {
        _cc = chartThemeColors();
        __apLineW = (_cc.lw === 'light') ? 2.0 : 1.9;
        // 描边宽度 = 线宽 × 0.15；必须远小于线宽，接近线宽会把彩色线盖住
        __apGlowMode = _cc.glow;
        __apGlowPad = (_cc.glow === 'none') ? 0 : __apLineW * 0.10;
        resizeCanvas();

        var ctx = cv.getContext("2d");

        // 顺序关键：先彩色描粗一圈露边，再暗色描回原宽压住中间（反过来会整条盖住彩色）
        function strokeWithGlow() {
            if (__apGlowPad <= 0) { ctx.stroke(); return; }
            var w = ctx.lineWidth, c = ctx.strokeStyle;
            // 描边色按当前线色即时算同色系加深/提亮；用固定黑或白会把线拉灰或洗白
            var edge = __apEdgeColor(c, __apGlowMode);
            ctx.lineWidth = w + __apGlowPad * 2;
            ctx.stroke();
            ctx.lineWidth = w;
            ctx.strokeStyle = edge;
            ctx.stroke();
            ctx.strokeStyle = c;
        }
        var oc = ovCv.getContext("2d");
        setCanvasTransform(ctx);

        // 硬币刻度标签布局常量
        var COIN_TICK_X = 8;
        var COIN_TICK_BASELINE = 4;
        var COIN_TICK_STACK_GAP = 11;

        pad = { t: 20, r: showCoins ? 110 : 20, b: 52, l: 52 };
        gW = W - pad.l - pad.r;
        gH = H - pad.t - pad.b;

        // ---- 主数据范围（体力轴，最小值固定 0） ----
        var allMin = 0, allMax = -Infinity;
        if (chartType === 'line') {
            for (var i = 0; i < nn; i++) {
                if (ap[i] > allMax) allMax = ap[i];
            }
        } else {
            for (var i = 0; i < nn; i++) {
                if (highs[i] > allMax) allMax = highs[i];
            }
        }
        if (allMax === -Infinity) allMax = 100;
        var allRng = allMax - allMin || 1;
        allMax += allRng * 0.08;

        // ---- 黄币独立范围 ----
        var yellowMin = Infinity, yellowMax = -Infinity;
        var yellowCoinsLen = yellowCoins ? yellowCoins.length : 0;
        var hasYellowCoins = showCoins && chartType === 'line' && yellowCoinsLen > 0;
        if (hasYellowCoins) {
            for (var i = 0; i < yellowCoinsLen; i++) {
                if (yellowCoins[i] === null || yellowCoins[i] === undefined) continue;
                if (yellowCoins[i] < yellowMin) yellowMin = yellowCoins[i];
                if (yellowCoins[i] > yellowMax) yellowMax = yellowCoins[i];
            }
            if (yellowMin === Infinity) yellowMin = 0;
            if (yellowMax === -Infinity) yellowMax = 1000;
            var yellowRng = yellowMax - yellowMin || 1;
            yellowMin -= yellowRng * 0.08;
            yellowMax += yellowRng * 0.08;
        }

        // ---- 紫币独立范围（最小值固定 0） ----
        var purpleMin = 0, purpleMax = -Infinity;
        var purpleCoinsLen = purpleCoins ? purpleCoins.length : 0;
        var hasPurpleCoins = showCoins && chartType === 'line' && purpleCoinsLen > 0;
        if (hasPurpleCoins) {
            for (var i = 0; i < purpleCoinsLen; i++) {
                if (purpleCoins[i] === null || purpleCoins[i] === undefined) continue;
                if (purpleCoins[i] > purpleMax) purpleMax = purpleCoins[i];
            }
            if (purpleMax === -Infinity) purpleMax = 1000;
            var purpleRng = purpleMax - purpleMin || 1;
            purpleMax += purpleRng * 0.08;
        }

        // ---- 紫币独立轴 ----
        var hasPurpleAxis = showCoins && chartType === 'line' && hasPurpleCoins;
        // ---- 组合轴（黄币 + 资产共用） ----
        var hasCombined = showCoins && chartType === 'line' && (hasYellowCoins || hasAssetSeries || hasDistanceSeries);
        var hasExtra = hasPurpleAxis || hasCombined;
        var combinedMin = 0, combinedMax = -Infinity;
        function scanRange(arr) {
            for (var i = 0; i < arr.length; i++) {
                if (arr[i] === null || arr[i] === undefined) continue;
                if (arr[i] > combinedMax) combinedMax = arr[i];
            }
        }
        if (hasCombined) {
            if (hasYellowCoins) scanRange(yellowCoins);
            if (hasAssetSeries) scanRange(lineAsset);
            if (hasDistanceSeries) scanRange(lineDistance);
            if (combinedMax === -Infinity) combinedMax = 1000;
            var combinedRng = combinedMax - combinedMin || 1;
            combinedMax += combinedRng * 0.08;
        }

        // 刻度配置（右侧标签）：第1行紫币独立，第2行黄币代表合并轴
        var EXTRA_SERIES_CONFIGS = [];
        var cfgOffset = 0;
        function addCfg(has, color, dataMin, dataMax) {
            if (!has) return;
            EXTRA_SERIES_CONFIGS.push({ color: color, dataMin: dataMin, dataMax: dataMax, offsetY: cfgOffset });
            cfgOffset += COIN_TICK_STACK_GAP;
        }
        addCfg(hasPurpleCoins, __apSeriesColor(3), purpleMin, purpleMax);
        addCfg(hasCombined, __apSeriesColor(2), combinedMin, combinedMax);

        // 每个系列必须带 color：光晕用同色阴影画，缺了会收到 undefined 而不出现
        var SERIES_DRAW = [
            // 这里一度按无障碍指南给系列分配不同虚线做「冗余编码」，
            // 不用虚线做「冗余编码」：每像素约 6 个点，短虚线会碎成断点反而更难读
            { has: hasPurpleCoins, data: purpleCoins, yFn: yOfPurple, dash: [],
              color: __apSeriesColor(3) },
            { has: hasYellowCoins, data: yellowCoins, yFn: yOfCombined,
              dash: [], color: __apSeriesColor(2) },
            { has: hasAssetSeries, data: lineAsset, ts: lineAssetTs,
              yFn: yOfCombined, dash: [], color: __apSeriesColor(4) },
            { has: hasDistanceSeries, data: lineDistance, yFn: yOfCombined,
              dash: [], color: __apSeriesColor(5) },
        ];

        // Y 坐标映射
        function yScale(value, rangeMin, rangeMax) {
            return pad.t + gH - (value - rangeMin) / (rangeMax - rangeMin) * gH;
        }
        function yOf(v) { return yScale(v, allMin, allMax); }
        function yOfPurple(v) { return yScale(v, purpleMin, purpleMax); }
        function yOfCombined(v) { return yScale(v, combinedMin, combinedMax); }

        // 时间感知的 x 坐标映射
        function xOfLine(i) {
            return pad.l + (i / Math.max(nn - 1, 1)) * gW;
        }

        function drawAssetTicks(ctx, yOfMain, mainMin, mainMax) {
            if (!hasExtra) return;
            ctx.font = "10px -apple-system, sans-serif";
            ctx.textAlign = "left";
            for (var i = 0; i <= 5; i++) {
                var mainVal = mainMin + (mainMax - mainMin) * (i / 5);
                var y = yOfMain(mainVal);
                for (var ci = 0; ci < EXTRA_SERIES_CONFIGS.length; ci++) {
                    var cfg = EXTRA_SERIES_CONFIGS[ci];
                    var val = cfg.dataMin + (cfg.dataMax - cfg.dataMin) * (i / 5);
                    ctx.fillStyle = cfg.color;
                    ctx.fillText(Math.round(val), W - pad.r + COIN_TICK_X, y + COIN_TICK_BASELINE + cfg.offsetY);
                }
            }
        }

        // ---- 绘制系列线（紫币独立 Y 轴，黄币/资产共用组合 Y 轴） ----
        function drawSeriesLine(xOf, start, end) {
            for (var ci = 0; ci < SERIES_DRAW.length; ci++) {
                var sd = SERIES_DRAW[ci];
                if (!sd.has) continue;
                if (!seriesVisible[ci + 1]) continue;

                ctx.lineWidth = __apLineW;
                ctx.lineJoin = "round";
                ctx.setLineDash(sd.dash);
                ctx.strokeStyle = sd.color;
                ctx.beginPath();
                var started = false;

                for (var i = start; i < end && i < sd.data.length; i++) {
                    if (sd.data[i] === null || sd.data[i] === undefined) { started = false; continue; }
                    var x = xOf(i), y = sd.yFn(sd.data[i]);
                    if (!started) { ctx.moveTo(x, y); started = true; }
                    else { ctx.lineTo(x, y); }
                }
                strokeWithGlow();
            }
            ctx.setLineDash([]);
        }

        var candleSpace = gW / nn;
        var candleW = Math.max(3, Math.min(candleSpace * 0.6, 30));
        function xCenter(i) { return pad.l + candleSpace * (i + 0.5); }

        // ======== 初始绘制（非缩放全量视图） ========
        // 先复位变换再清屏：变换会累积，复位前清屏会留下未清的旧像素（重影）
        setCanvasTransform(ctx);
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = _cc.bg;
        ctx.fillRect(0, 0, W, H);

        ctx.strokeStyle = _cc.grid;
        ctx.lineWidth = 1;
        ctx.fillStyle = _cc.label;
        ctx.font = "11px -apple-system, sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (var i = 0; i <= 5; i++) {
            var v = allMin + (allMax - allMin) * (i / 5);
            var y = yOf(v);
            ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
            ctx.fillText(Math.round(v), pad.l - 8, y);
        }

        drawAssetTicks(ctx, yOf, allMin, allMax);

        var avgY = yOf(avg);
        ctx.save();
        ctx.strokeStyle = __apSeriesColor('avg');
        ctx.lineWidth = 1;
        ctx.setLineDash([6, 4]);
        ctx.beginPath(); ctx.moveTo(pad.l, avgY); ctx.lineTo(W - pad.r, avgY); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = __apSeriesColor('avg');
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(I18N.ChartMean + avg, W - pad.r - 4, avgY - 8);

        ctx.fillStyle = _cc.label;
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        if (chartType === 'line') {
            var labelStep = Math.max(1, Math.floor(nn / 8));
            for (var i = 0; i < nn; i += labelStep) {
                ctx.save();
                ctx.translate(xOfLine(i), H - pad.b + 8);
                ctx.rotate(0.4);
                ctx.fillText(labels[i], 0, 0);
                ctx.restore();
            }
        } else {
            var labelStep = Math.max(1, Math.floor(nn / 12));
            for (var i = 0; i < nn; i += labelStep) {
                ctx.fillText(labels[i], xCenter(i), H - pad.b + 8);
            }
        }

        if (chartType === 'line' && seriesVisible[0]) {
            // 体力主线是主序列，加宽以免与网格线同粗
            ctx.lineWidth = __apLineW * 1.2;
            ctx.lineJoin = "round";
            for (var i = 1; i < nn; i++) {
                ctx.beginPath();
                ctx.moveTo(xOfLine(i - 1), yOf(ap[i - 1]));
                ctx.strokeStyle = ap[i] >= ap[i - 1] ? _cc.up : _cc.down;
                ctx.lineTo(xOfLine(i), yOf(ap[i]));
                strokeWithGlow();
            }
            if (nn < 60) {
                for (var i = 0; i < nn; i++) {
                    ctx.beginPath();
                    ctx.arc(xOfLine(i), yOf(ap[i]), 1.5 * __apLineW, 0, Math.PI * 2);
                    var dotColor = (i > 0 && ap[i] < ap[i - 1]) ? _cc.down : _cc.up;
                    ctx.fillStyle = dotColor;
                    ctx.fill();
                }
            }
        } else if (seriesVisible[0]) {
            for (var i = 0; i < nn; i++) {
                var cx = xCenter(i);
                var o = opens[i], h = highs[i], l = lows[i], c = closes[i];
                var isUp = c > o;
                var isDown = c < o;
                var isFlat = c === o;
                var color = isFlat ? "#888" : (isUp ? _cc.up : _cc.down);

                ctx.strokeStyle = color;
                ctx.lineWidth = __apLineW * 1.5;
                ctx.beginPath();
                ctx.moveTo(cx, yOf(h));
                ctx.lineTo(cx, yOf(l));
                ctx.stroke();

                var bodyTop = yOf(Math.max(o, c));
                var bodyBot = yOf(Math.min(o, c));
                var bodyH = Math.max(bodyBot - bodyTop, 1);

                if (isUp || isDown) {
                    ctx.fillStyle = color;
                    ctx.fillRect(cx - candleW / 2, bodyTop, candleW, bodyH);
                } else {
                    ctx.beginPath();
                    ctx.moveTo(cx - candleW / 2, yOf(o));
                    ctx.lineTo(cx + candleW / 2, yOf(o));
                    ctx.stroke();
                }
            }

            function drawMA(days, maColor) {
                if (nn < days) return;
                ctx.beginPath();
                ctx.lineWidth = __apLineW * 1.5;
                ctx.strokeStyle = maColor;
                var started = false;
                for (var i = days - 1; i < nn; i++) {
                    var sum = 0;
                    for (var j = 0; j < days; j++) sum += closes[i - j];
                    var maVal = sum / days;
                    var x = xCenter(i), y = yOf(maVal);
                    if (!started) { ctx.moveTo(x, y); started = true; }
                    else { ctx.lineTo(x, y); }
                }
                strokeWithGlow();
            }
            drawMA(5, __apSeriesColor('ma5'));
            drawMA(10, __apSeriesColor('ma10'));
        }

        // 绘制额外系列线（黄币/紫币/资产）
        drawSeriesLine(xOfLine, 0, nn);

        // ======== 鼠标交互：十字线 + 滚珠 + 提示框 ========
        addListener(cv, "mousemove", function (e) {
            if (_isSelecting) return;
            var rect = cv.getBoundingClientRect();
            var mx_ = e.clientX - rect.left;
            var my_ = e.clientY - rect.top;

            oc.setTransform(1, 0, 0, 1, 0, 0);
            oc.clearRect(0, 0, ovCv.width, ovCv.height);

            if (mx_ < pad.l || mx_ > W - pad.r || my_ < pad.t || my_ > pad.t + gH) {
                tipEl.style.display = "none";
                return;
            }

            oc.scale(dpr, dpr);

            if (chartType === 'line') {
                var visibleStart = Math.max(0, Math.floor(panOffset));
                var visibleCount = Math.ceil(nn / zoomLevel);
                var visibleEnd = Math.min(nn, visibleStart + visibleCount);
                var visibleNn = visibleEnd - visibleStart;

                var dMin = 0, dMax = -Infinity;
                for (var i = visibleStart; i < visibleEnd; i++) {
                    if (ap[i] > dMax) dMax = ap[i];
                }
                if (dMax === -Infinity) dMax = 100;
                var drng = dMax - dMin || 1;
                dMax += drng * 0.1;

                var xScale = gW / Math.max(visibleNn - 1, 1);

                // 等距索引定位（与 xOfLine 视觉渲染一致）
                var idx = Math.round(visibleStart + (mx_ - pad.l) / gW * (visibleNn - 1));
                idx = Math.max(0, Math.min(nn - 1, idx));

                // 等距索引的十字线 x 位置
                var px = pad.l + ((idx - visibleStart) / Math.max(visibleNn - 1, 1)) * gW;
                if (seriesVisible[0]) {
                var py = yScale(ap[idx], dMin, dMax);

                oc.strokeStyle = "rgba(255,255,255,0.18)";
                oc.lineWidth = 1;
                oc.setLineDash([4, 3]);
                oc.beginPath(); oc.moveTo(px, pad.t); oc.lineTo(px, pad.t + gH); oc.stroke();
                oc.beginPath(); oc.moveTo(pad.l, py); oc.lineTo(W - pad.r, py); oc.stroke();
                oc.setLineDash([]);

                oc.beginPath(); oc.arc(px, py, 6, 0, Math.PI * 2);
                oc.fillStyle = "rgba(100,181,246,0.3)"; oc.fill();
                oc.beginPath(); oc.arc(px, py, 4, 0, Math.PI * 2);
                oc.fillStyle = __apSeriesColor(1); oc.fill();
                oc.strokeStyle = "#fff"; oc.lineWidth = 2; oc.stroke();
                }

                // ---- 滚珠：紫币（独立轴）+ 黄币/资产（共用轴） ----
                function hexToRgba(hex, alpha) {
                    var r = parseInt(hex.slice(1, 3), 16);
                    var g = parseInt(hex.slice(3, 5), 16);
                    var b = parseInt(hex.slice(5, 7), 16);
                    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
                }
                function drawBead(val, color, yFn) {
                    var by = yFn(val);
                    oc.beginPath(); oc.arc(px, by, 5, 0, Math.PI * 2);
                    oc.fillStyle = hexToRgba(color, 0.3); oc.fill();
                    oc.beginPath(); oc.arc(px, by, 3, 0, Math.PI * 2);
                    oc.fillStyle = color; oc.fill();
                    oc.strokeStyle = "#fff"; oc.lineWidth = 1.5; oc.stroke();
                }
                if (hasPurpleCoins && idx < purpleCoinsLen && purpleCoins[idx] !== null && purpleCoins[idx] !== undefined && seriesVisible[1])
                    drawBead(purpleCoins[idx], __apSeriesColor(3), yOfPurple);
                if (hasYellowCoins && idx < yellowCoinsLen && yellowCoins[idx] !== null && yellowCoins[idx] !== undefined && seriesVisible[2])
                    drawBead(yellowCoins[idx], __apSeriesColor(2), yOfCombined);

                if (seriesVisible[3] && hasAssetSeries) {
                    var closestIdx_a = -1, closestDist_a = 600000;
                    for (var j = 0; j < lineAssetTs.length; j++) {
                        var dist = Math.abs(idx - j);
                        if (dist < closestDist_a) { closestDist_a = dist; closestIdx_a = j; }
                    }
                    if (closestIdx_a !== -1 && closestDist_a < 5)
                        drawBead(lineAsset[closestIdx_a], __apSeriesColor(4), yOfCombined);
                }

                // 海里数 bead
                if (hasDistanceSeries && idx < lineDistance.length && lineDistance[idx] !== null && lineDistance[idx] !== undefined && seriesVisible[4])
                    drawBead(lineDistance[idx], __apSeriesColor(5), yOfCombined);

                oc.setTransform(1, 0, 0, 1, 0, 0);

                var diff = idx > 0 ? (ap[idx] - ap[idx - 1]) : 0;
                var isUp = diff >= 0;
                var dc = isUp ? _cc.up : _cc.down;
                var ds = (isUp ? "+" : "") + diff;
                var tooltipRows = [
                    { style: { color: "#888", marginBottom: "4px", fontWeight: "600" }, parts: [{ type: 'text', value: labels[idx] }] },
                ];
                if (seriesVisible[0]) {
                tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartAp }, { type: 'bold', value: String(ap[idx]), style: { color: __apSeriesColor(1) } }] },
                    { parts: [{ type: 'text', value: I18N.ChartDelta }, { type: 'bold', value: ds, style: { color: dc } }] });
                }

                if (isDetailMode) {
                    var source = sources && sources[idx] ? sources[idx] : '-';
                    var sourceColor = source === 'cl1' ? __apSeriesColor(1) : (source === 'meow' ? __apSeriesColor('avg') : '#888');
                    tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartSource }, { type: 'bold', value: source, style: { color: sourceColor } }] });
                }

                // 黄币 tooltip
                if (seriesVisible[2] && hasYellowCoins && idx < yellowCoinsLen && yellowCoins[idx] !== null && yellowCoins[idx] !== undefined) {
                    var yc = yellowCoins[idx];
                    var ycDiff = idx > 0 && yellowCoins[idx - 1] !== null && yellowCoins[idx - 1] !== undefined ? (yc - yellowCoins[idx - 1]) : 0;
                    var ycColor = ycDiff >= 0 ? _cc.up : _cc.down;
                    var ycDiffStr = (ycDiff >= 0 ? "+" : "") + ycDiff;
                    tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartYellow }, { type: 'bold', value: String(yc), style: { color: __apSeriesColor(2) } }, { type: 'text', value: " (" + ycDiffStr + ")", style: { color: ycColor } }] });
                }

                // 紫币 tooltip
                if (seriesVisible[1] && hasPurpleCoins && idx < purpleCoinsLen && purpleCoins[idx] !== null && purpleCoins[idx] !== undefined) {
                    var pc = purpleCoins[idx];
                    var pcDiff = idx > 0 && purpleCoins[idx - 1] !== null && purpleCoins[idx - 1] !== undefined ? (pc - purpleCoins[idx - 1]) : 0;
                    var pcColor = pcDiff >= 0 ? _cc.up : _cc.down;
                    var pcDiffStr = (pcDiff >= 0 ? "+" : "") + pcDiff;
                    tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartPurple }, { type: 'bold', value: String(pc), style: { color: __apSeriesColor(3) } }, { type: 'text', value: " (" + pcDiffStr + ")", style: { color: pcColor } }] });
                }

                // 资产 tooltip
                if (seriesVisible[3] && hasAssetSeries) {
                    var closestIdx = -1, closestDist = 600000;
                    for (var j = 0; j < lineAssetTs.length; j++) {
                        var dist = Math.abs(idx - j);
                        if (dist < closestDist) { closestDist = dist; closestIdx = j; }
                    }
                    if (closestIdx !== -1 && closestDist < 5) {
                        tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartAsset }, { type: 'bold', value: lineAsset[closestIdx].toFixed(1), style: { color: __apSeriesColor(4) } }] });
                    }
                }

                // 海里数 tooltip
                if (seriesVisible[4] && hasDistanceSeries && idx < lineDistance.length && lineDistance[idx] !== null && lineDistance[idx] !== undefined) {
                    var d = lineDistance[idx];
                    var dDiff = idx > 0 && lineDistance[idx - 1] !== null && lineDistance[idx - 1] !== undefined ? (d - lineDistance[idx - 1]) : 0;
                    var dColor = dDiff >= 0 ? _cc.up : _cc.down;
                    var dDiffStr = (dDiff >= 0 ? "+" : "") + dDiff;
                    tooltipRows.push({ parts: [{ type: 'text', value: I18N.ChartDistance }, { type: 'bold', value: String(d), style: { color: __apSeriesColor(5) } }, { type: 'text', value: " (" + dDiffStr + ")", style: { color: dColor } }] });
                }

                setTooltipContent(tipEl, tooltipRows);
            } else {
                // K线图的鼠标交互（与上游一致）
                var idx = Math.floor((mx_ - pad.l) / candleSpace);
                idx = Math.max(0, Math.min(nn - 1, idx));
                var cx = xCenter(idx);

                oc.strokeStyle = "rgba(255,255,255,0.18)";
                oc.lineWidth = 1;
                oc.setLineDash([4, 3]);
                oc.beginPath(); oc.moveTo(cx, pad.t); oc.lineTo(cx, pad.t + gH); oc.stroke();
                oc.beginPath(); oc.moveTo(pad.l, my_); oc.lineTo(W - pad.r, my_); oc.stroke();
                oc.setLineDash([]);

                oc.strokeStyle = "#fff";
                oc.lineWidth = 1;
                oc.globalAlpha = 0.15;
                oc.fillStyle = "#fff";
                oc.fillRect(cx - candleW / 2 - 2, pad.t, candleW + 4, gH);
                oc.globalAlpha = 1.0;
                oc.setTransform(1, 0, 0, 1, 0, 0);

                var o = opens[idx], h = highs[idx], l = lows[idx], c_ = closes[idx];
                var chg = c_ - o;
                var chgPct = o !== 0 ? ((chg / o) * 100).toFixed(1) : "0.0";
                var isUp = c_ >= o;
                var dc = isUp ? _cc.up : _cc.down;
                var chgSign = chg >= 0 ? "+" : "";

                var ma5Val = "-";
                if (idx >= 4) {
                    var sum5 = 0; for (var j = 0; j < 5; j++) sum5 += closes[idx - j];
                    ma5Val = (sum5 / 5).toFixed(1);
                }
                var ma10Val = "-";
                if (idx >= 9) {
                    var sum10 = 0; for (var j = 0; j < 10; j++) sum10 += closes[idx - j];
                    ma10Val = (sum10 / 10).toFixed(1);
                }

                setTooltipContent(tipEl, [
                    { style: { color: "#888", marginBottom: "4px", fontWeight: "600" }, parts: [{ type: 'text', value: labels[idx] }] },
                    {
                        parts: [
                            { type: 'text', value: I18N.ChartOpen },
                            { type: 'bold', value: String(o) },
                            { type: 'text', value: I18N.ChartMa5 + ma5Val, style: { marginLeft: "8px", color: __apSeriesColor('ma5') } }
                        ]
                    },
                    {
                        parts: [
                            { type: 'text', value: I18N.ChartClose },
                            { type: 'bold', value: String(c_), style: { color: dc } },
                            { type: 'text', value: I18N.ChartMa10 + ma10Val, style: { marginLeft: "8px", color: __apSeriesColor('ma10') } }
                        ]
                    },
                    { parts: [{ type: 'text', value: I18N.ChartHigh }, { type: 'bold', value: String(h), style: { color: _cc.up } }] },
                    { parts: [{ type: 'text', value: I18N.ChartLow }, { type: 'bold', value: String(l), style: { color: _cc.down } }] },
                    { parts: [{ type: 'text', value: I18N.ChartChange }, { type: 'bold', value: chgSign + chg + " (" + chgSign + chgPct + "%)", style: { color: dc } }] },
                    { style: { color: "#666", marginTop: "4px" }, parts: [{ type: 'text', value: I18N.ChartDensity + counts[idx] }] }
                ]);
            }

            tipEl.style.display = "block";
            var tx = (chartType === 'line' ? px : cx) + 18;
            var ty = my_ - 60;
            if (tx + 180 > W) tx = (chartType === 'line' ? px : cx) - 200;
            if (ty < 8) ty = my_ + 18;
            tipEl.style.left = tx + "px";
            tipEl.style.top = ty + "px";
        });

        addListener(cv, "mouseleave", function () {
            tipEl.style.display = "none";
            oc.setTransform(1, 0, 0, 1, 0, 0);
            oc.clearRect(0, 0, ovCv.width, ovCv.height);
        });

        // ======== 图例点击切换曲线 ========
        var legendId = chartId + "_legend";
        var legendEl = document.getElementById(legendId);
        if (legendEl) {
            if (legendEl._legendHandler) {
                legendEl.removeEventListener("click", legendEl._legendHandler);
            }
            legendEl._legendHandler = function (e) {
                var item = e.target.closest(".ap-legend-item");
                if (!item) return;
                var idx = parseInt(item.getAttribute("data-series"), 10);
                if (isNaN(idx) || idx < 0 || idx >= seriesVisible.length) return;
                // 独立切换：只开关当前点中的序列，不影响其他
                seriesVisible[idx] = !seriesVisible[idx];
                // 确保至少一条序列可见
                var anyVisible = false;
                for (var si = 0; si < seriesVisible.length; si++) {
                    if (seriesVisible[si]) { anyVisible = true; break; }
                }
                if (!anyVisible) {
                    for (var si = 0; si < seriesVisible.length; si++) seriesVisible[si] = true;
                }
                legendEl.querySelectorAll(".ap-legend-item").forEach(function (li, i) {
                    var si = parseInt(li.getAttribute("data-series"), 10);
                    li.style.opacity = seriesVisible[si] ? "1" : "0.35";
                });
                (chartType === 'line' && typeof renderDetailChart === 'function' ? renderDetailChart : initChart)();
            };
            addListener(legendEl, "click", legendEl._legendHandler);
            legendEl.querySelectorAll(".ap-legend-item").forEach(function (li, i) {
                var si = parseInt(li.getAttribute("data-series"), 10);
                li.style.opacity = seriesVisible[si] ? "1" : "0.35";
            });
        }

        // ======== 缩放/平移（仅 line 图） ========
        if (chartType === 'line') {
            var zoomLevel = 1.0;
            var panOffset = 0;
            var maxZoom = 5.0;
            var minZoom = 0.5;

            function renderDetailChart() {
                _cc = chartThemeColors();
        __apLineW = (_cc.lw === 'light') ? 2.0 : 1.9;
        // 描边宽度 = 线宽 × 0.15；必须远小于线宽，接近线宽会把彩色线盖住
        __apGlowMode = _cc.glow;
        __apGlowPad = (_cc.glow === 'none') ? 0 : __apLineW * 0.10;
                resizeCanvas();
                var visibleStart = Math.max(0, Math.floor(panOffset));
                var visibleCount = Math.ceil(nn / zoomLevel);
                var visibleEnd = Math.min(nn, visibleStart + visibleCount);
                var visibleNn = visibleEnd - visibleStart;

                var dMin = 0, dMax = -Infinity;
                for (var i = visibleStart; i < visibleEnd; i++) {
                    if (ap[i] > dMax) dMax = ap[i];
                }
                if (dMax === -Infinity) dMax = 100;
                var drng = dMax - dMin || 1;
                dMax += drng * 0.1;

                // 先复位变换再清屏，否则残留旧像素（重影）
                setCanvasTransform(ctx);
                ctx.clearRect(0, 0, W, H);
                ctx.fillStyle = _cc.bg;
                ctx.fillRect(0, 0, W, H);

                ctx.strokeStyle = _cc.grid;
                ctx.lineWidth = 1;
                ctx.fillStyle = _cc.label;
                ctx.font = "11px -apple-system, sans-serif";
                ctx.textAlign = "right";
                ctx.textBaseline = "middle";
                for (var i = 0; i <= 5; i++) {
                    var v = dMin + (dMax - dMin) * (i / 5);
                    var y = yScale(v, dMin, dMax);
                    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
                    ctx.fillText(Math.round(v), pad.l - 8, y);
                }

                var xScale = gW / Math.max(visibleNn - 1, 1);
                function dxOf(i) {
                    return pad.l + (i - visibleStart) * xScale;
                }
                function dyOf(v) { return yScale(v, dMin, dMax); }

                drawAssetTicks(ctx, dyOf, dMin, dMax);

                // Ap 线
                if (seriesVisible[0]) {
                ctx.lineWidth = __apLineW * 1.2;
                ctx.lineJoin = "round";
                for (var i = visibleStart + 1; i < visibleEnd; i++) {
                    ctx.beginPath();
                    ctx.moveTo(dxOf(i - 1), dyOf(ap[i - 1]));
                    ctx.strokeStyle = ap[i] >= ap[i - 1] ? _cc.up : _cc.down;
                    ctx.lineTo(dxOf(i), dyOf(ap[i]));
                    strokeWithGlow();
                }

                // Ap 数据点
                var dotInterval = Math.max(1, Math.floor(visibleNn / 50));
                for (var i = visibleStart; i < visibleEnd; i += dotInterval) {
                    ctx.beginPath();
                    ctx.arc(dxOf(i), dyOf(ap[i]), 1.5, 0, Math.PI * 2);
                    var dotColor = (i > visibleStart && ap[i] < ap[i - 1]) ? _cc.down : _cc.up;
                    ctx.fillStyle = dotColor;
                    ctx.fill();
                }
                }

                // 绘制额外系列线
                drawSeriesLine(dxOf, visibleStart, visibleEnd);

                // X 轴标签
                var labelInterval = Math.max(1, Math.floor(visibleNn / 8));
                for (var i = visibleStart; i < visibleEnd; i += labelInterval) {
                    var lx = dxOf(i);
                    ctx.save();
                    ctx.translate(lx, H - pad.b + 8);
                    ctx.rotate(0.3);
                    ctx.fillText(labels[i], 0, 0);
                    ctx.restore();
                }
            }

            renderDetailChart();

            var isDragging = false;
            var dragStartX = 0;
            var dragStartPan = 0;
            var selStartX = 0;

            addListener(cv, "mousedown", function (e) {
                if (e.button !== 0) return;
                var rect = cv.getBoundingClientRect();
                var my = e.clientY - rect.top;
                isDragging = true;
                dragStartX = e.clientX;
            if (my <= H - 40) {
                // 图表区域 -> 选区缩放（不检查缩放状态，始终可选区）
                _isSelecting = true;
                selStartX = e.clientX;
                cv.style.cursor = "crosshair";
            } else {
                // 底部时间轴区域 -> 拖动平移
                _isSelecting = false;
                dragStartPan = panOffset;
                cv.style.cursor = "grabbing";
            }
            });

            addListener(document, "mousemove", function (e) {
                if (!isDragging) return;
                if (_isSelecting) {
                    // 选区矩形占满图表高度
                    var rect = cv.getBoundingClientRect();
                    var mx = e.clientX - rect.left;
                    var sx = selStartX - rect.left;

                    oc.setTransform(1, 0, 0, 1, 0, 0);
                    oc.clearRect(0, 0, ovCv.width, ovCv.height);
                    oc.scale(dpr, dpr);

                    var rx = Math.min(sx, mx);
                    var rw = Math.abs(mx - sx);

                    oc.fillStyle = "rgba(100, 181, 246, 0.08)";
                    oc.fillRect(rx, pad.t, rw, gH);
                    oc.strokeStyle = "rgba(100, 181, 246, 0.5)";
                    oc.lineWidth = 1.5;
                    oc.setLineDash([4, 3]);
                    oc.strokeRect(rx, pad.t, rw, gH);
                    oc.setLineDash([]);
                } else {
                    var dx = e.clientX - dragStartX;
                    var visibleCount = Math.ceil(nn / zoomLevel);
                    var xScale = gW / Math.max(visibleCount - 1, 1);
                    var newPan = dragStartPan - dx / xScale;
                    var maxPan = Math.max(0, nn - visibleCount);
                    panOffset = Math.max(0, Math.min(maxPan, newPan));
                    renderDetailChart();
                }
            });

            addListener(document, "mouseup", function (e) {
                if (!isDragging) return;
                isDragging = false;

                if (_isSelecting) {
                    _isSelecting = false;
                    var rect = cv.getBoundingClientRect();
                    var mx = e.clientX - rect.left;
                    var dragPx = Math.abs(mx - (selStartX - rect.left));

                    if (dragPx > 15) {
                        var x1 = Math.max(pad.l, Math.min(W - pad.r, selStartX - rect.left));
                        var x2 = Math.max(pad.l, Math.min(W - pad.r, mx));
                        var startPx = Math.min(x1, x2);
                        var endPx = Math.max(x1, x2);

                        var startIdx = Math.round(panOffset + (startPx - pad.l) / gW * (Math.ceil(nn / zoomLevel) - 1));
                        var endIdx = Math.round(panOffset + (endPx - pad.l) / gW * (Math.ceil(nn / zoomLevel) - 1));
                        startIdx = Math.max(0, Math.min(nn - 1, startIdx));
                        endIdx = Math.max(0, Math.min(nn - 1, endIdx));

                        if (endIdx > startIdx) {
                            panOffset = startIdx;
                            zoomLevel = nn / (endIdx - startIdx);
                            renderDetailChart();
                        }
                    }

                    oc.setTransform(1, 0, 0, 1, 0, 0);
                    oc.clearRect(0, 0, ovCv.width, ovCv.height);
                }

                cv.style.cursor = "crosshair";
            });

            addListener(cv, "wheel", function (e) {
                e.preventDefault();
                var rect = cv.getBoundingClientRect();
                var mx = e.clientX - rect.left;
                var zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
                var newZoom = Math.max(minZoom, Math.min(maxZoom, zoomLevel * zoomFactor));
                if (newZoom !== zoomLevel) {
                    var visibleCountBefore = Math.ceil(nn / zoomLevel);
                    var visibleCountAfter = Math.ceil(nn / newZoom);
                    var xScaleBefore = gW / Math.max(visibleCountBefore - 1, 1);
                    var mouseIdx = panOffset + (mx - pad.l) / xScaleBefore;
                    zoomLevel = newZoom;
                    var xScaleAfter = gW / Math.max(visibleCountAfter - 1, 1);
                    panOffset = Math.max(0, mouseIdx - (mx - pad.l) / xScaleAfter);
                    var maxPan = Math.max(0, nn - visibleCountAfter);
                    panOffset = Math.max(0, Math.min(maxPan, panOffset));
                    renderDetailChart();
                }
            }, { passive: false });

            addListener(cv, "dblclick", function () {
                zoomLevel = 1.0;
                panOffset = 0;
                renderDetailChart();
            });

            var zoomInBtn = document.getElementById(chartId + "_zoom_in");
            var zoomOutBtn = document.getElementById(chartId + "_zoom_out");
            var zoomResetBtn = document.getElementById(chartId + "_reset");

            if (zoomInBtn) {
                addListener(zoomInBtn, "click", function () {
                    zoomLevel = Math.min(maxZoom, zoomLevel * 1.5);
                    var visibleCount = Math.ceil(nn / zoomLevel);
                    var maxPan = Math.max(0, nn - visibleCount);
                    panOffset = Math.min(panOffset, maxPan);
                    renderDetailChart();
                });
            }

            if (zoomOutBtn) {
                addListener(zoomOutBtn, "click", function () {
                    zoomLevel = Math.max(minZoom, zoomLevel / 1.5);
                    renderDetailChart();
                });
            }

            if (zoomResetBtn) {
                addListener(zoomResetBtn, "click", function () {
                    zoomLevel = 1.0;
                    panOffset = 0;
                    renderDetailChart();
                });
            }
        }
    }
})();
