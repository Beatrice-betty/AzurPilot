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

    var labels = __LABELS__;
    var seriesData = __SERIES_DATA__;  // array of {key, name, data} where data[i] is number or null
    var chartId = "__CHART_ID__";
    var chartTitle = "__CHART_TITLE__";
    var zoomLevel = 1.0;
    var panOffset = 0;
    var maxZoom = 5.0;
    var minZoom = 0.5;
    var _isSelecting = false;

    var cv = document.getElementById(chartId);
    if (!cv) return;
    var tipEl = document.getElementById(chartId + "_tip");
    var ovCv = document.getElementById(chartId + "_ov");
    var legendEl = document.getElementById(chartId + "_legend");

    var nn = labels.length;
    if (nn < 1) return;

    var dpr = window.devicePixelRatio || 1;
    var W, H, pad, gW, gH;
    var chartState = null;
    var cleanupHandlers = [];
    var initialRenderTimer = null;

    window.__resourceChartCleanups = window.__resourceChartCleanups || {};
    if (window.__resourceChartCleanups[chartId]) {
        window.__resourceChartCleanups[chartId]();
    }
    var cleanup = function () {
        cleanupHandlers.forEach(function (item) {
            if (item.type === "__disconnect") { item.target.disconnect(); return; }
            item.target.removeEventListener(item.type, item.handler, item.options);
        });
        cleanupHandlers = [];
        if (initialRenderTimer !== null) clearTimeout(initialRenderTimer);
        initialRenderTimer = null;
        if (window.__resourceChartCleanups[chartId] === cleanup) {
            delete window.__resourceChartCleanups[chartId];
        }
    };
    window.__resourceChartCleanups[chartId] = cleanup;

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


    function addListener(target, type, handler, options) {
        if (!target) return;
        target.addEventListener(type, handler, options);
        cleanupHandlers.push({ target: target, type: type, handler: handler, options: options });
    }

    function setCanvasTransform(ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function clearOverlay() {
        var oc = ovCv.getContext("2d");
        oc.setTransform(1, 0, 0, 1, 0, 0);
        oc.clearRect(0, 0, ovCv.width, ovCv.height);
        setCanvasTransform(oc);
        return oc;
    }

    function clampPanForZoom() {
        var visibleCount = Math.ceil(nn / zoomLevel);
        var maxPan = Math.max(0, nn - visibleCount);
        panOffset = Math.max(0, Math.min(maxPan, panOffset));
        return visibleCount;
    }

    // Resource display configs
    var resourceMeta = [];
    var seriesVisible = [];
    // 轴分配：左轴（主刻度）、右轴（彩色刻度）、隐藏轴（独立范围，不显示刻度）
    var RIGHT_AXIS_KEYS = { ActionPoint: 1, YellowCoin: 1, PurpleCoin: 1 };
    var HIDDEN_AXIS_KEYS = { Merit: 1 };
    for (var si = 0; si < seriesData.length; si++) {
        var key = seriesData[si].key;
        var side = 'left';
        if (RIGHT_AXIS_KEYS[key]) side = 'right';
        else if (HIDDEN_AXIS_KEYS[key]) side = 'hidden';
        resourceMeta.push({
            key: key,
            name: seriesData[si].name,
            color: seriesData[si].color,
            data: seriesData[si].data,
            side: side
        });
        seriesVisible.push(true);
    }

    initialRenderTimer = setTimeout(function () {
        initialRenderTimer = null;
        initChart();
    }, 300);

    function getVisibleDataIndices() {
        var indices = [];
        for (var i = 0; i < resourceMeta.length; i++) {
            if (seriesVisible[i]) indices.push(i);
        }
        return indices;
    }


    // 位图尺寸按 dpr 匹配显示尺寸。不要写 cv.style.width/height：canvas 是
    // width:100% 自适应，把量到的像素写回内联样式后会自我强化（宽度由自己
    // 上一步的值决定），容器变窄时报旧宽度、位图永不更新且无法恢复
    function resizeCanvas() {

        // 宽度取 getBoundingClientRect()：clientWidth 会四舍五入掉小数，差 1px 即被拉伸
        var rect = cv.getBoundingClientRect();
        var w = Math.round(rect.width);
        var h = Math.round(cv.clientHeight);
        if (!w) { w = Math.round(cv.clientWidth); }
        if (!h) { h = 400; }
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
                     up: '#ff6b6b', down: '#2ee6c5', lw: 'dark' };

    // 不用 shadowBlur：蒙一层阴影会让细线发糊，K 线影线尤其明显
    var __apLineW = 1;

    var _cc = __FALLBACK_CC;

    function chartThemeColors() {
        var cs = getComputedStyle(document.documentElement);
        function pick(n) { return (cs.getPropertyValue(n) || '').trim(); }
        var isInline = !!(cv && cv.closest
            && cv.closest('#pywebio-scope-stat_panels'));
        return {
            // 滚动栏用透明背景透出磨砂玻璃；二级菜单统计页保留主题底色
            bg: isInline ? 'rgba(0, 0, 0, 0)'
                : (pick('--alas-chart-bg') || pick('--ap-chart-container-bg')
                   || __FALLBACK_CC.bg),
            grid: pick('--alas-chart-grid') || __FALLBACK_CC.grid,
            label: pick('--alas-chart-label') || __FALLBACK_CC.label,
            up: pick('--alas-chart-up') || __FALLBACK_CC.up,
            down: pick('--alas-chart-down') || __FALLBACK_CC.down,
            lw: (cs.getPropertyValue('--alas-chart-lw') || '').trim()
                || __FALLBACK_CC.lw
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
        __apLineW = (_cc.lw === 'light') ? 2.3 : 1.9;
        resizeCanvas();

        var ctx = cv.getContext("2d");
        setCanvasTransform(ctx);
        var oc = ovCv.getContext("2d");

        pad = { t: 20, r: 110, b: 52, l: 56 };
        gW = W - pad.l - pad.r;
        gH = H - pad.t - pad.b;

        var visibleIndices = getVisibleDataIndices();

        // ---- 所有资源独立 Y 轴范围 ----
        var rightAxisCfgs = [];  // 右轴资源：用于彩色刻度
        var yFns = {};           // 全部 12 项资源的 Y 函数
        var gridMin = Infinity, gridMax = -Infinity;  // 网格/左轴参考范围
        (function () {
            for (var vi = 0; vi < visibleIndices.length; vi++) {
                var si = visibleIndices[vi];
                var meta = resourceMeta[si];
                if (yFns[meta.key]) continue;
                var mn = Infinity, mx = -Infinity;
                for (var di = 0; di < meta.data.length; di++) {
                    if (meta.data[di] === null || meta.data[di] === undefined) continue;
                    if (meta.data[di] < mn) mn = meta.data[di];
                    if (meta.data[di] > mx) mx = meta.data[di];
                }
                if (mn === Infinity) { mn = 0; mx = 100; }
                if (mx === mn) { mx = mn + 100; }
                var rng = mx - mn;
                var extra = Math.max(rng * 0.2, mn * 0.2, mx * 0.1);
                mn -= extra;
                mx += extra;
                mn = Math.max(0, mn);
                if (mn < gridMin) gridMin = mn;
                if (mx > gridMax) gridMax = mx;
                (function (rMin, rMax, rKey) {
                    yFns[rKey] = function (v) {
                        return pad.t + gH - (v - rMin) / (rMax - rMin) * gH;
                    };
                })(mn, mx, meta.key);
                if (meta.side === 'right') {
                    rightAxisCfgs.push({ key: meta.key, color: meta.color, range: { min: mn, max: mx }, offsetY: (rightAxisCfgs.length) * 12 });
                }
            }
        })();
        if (gridMin === Infinity) { gridMin = 0; gridMax = 100; }
        var gridRng = gridMax - gridMin;
        var gridExtra = Math.max(gridRng * 0.1, gridMin * 0.08);
        gridMin -= gridExtra;
        gridMax += gridExtra;
        gridMin = Math.max(0, gridMin);

        function yOfGrid(v) {
            return pad.t + gH - (v - gridMin) / (gridMax - gridMin) * gH;
        }
        function yOfForMeta(meta, v) {
            var fn = yFns[meta.key];
            return fn ? fn(v) : pad.t;
        }
        function xOf(i) {
            return pad.l + (i / Math.max(nn - 1, 1)) * gW;
        }

        chartState = {
            visibleStart: 0,
            visibleEnd: nn,
            visibleNn: nn,
            visibleIndices: visibleIndices,
            xOf: xOf,
            yOfForMeta: yOfForMeta
        };

        // ---- Draw background and grid ----
        // 先复位变换再清屏，否则残留旧像素（重影）
        setCanvasTransform(ctx);
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = _cc.bg;
        ctx.fillRect(0, 0, W, H);

        // Grid lines + 左轴参考刻度
        ctx.strokeStyle = _cc.grid;
        ctx.lineWidth = 1;
        ctx.fillStyle = _cc.label;
        ctx.font = "11px -apple-system, sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (var i = 0; i <= 5; i++) {
            var v = gridMin + (gridMax - gridMin) * (i / 5);
            var y = yOfGrid(v);
            ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
            ctx.fillText(fmtVal(v), pad.l - 8, y);
        }
        // ---- Right axis colored ticks ----
        if (rightAxisCfgs.length > 0) {
            ctx.font = "10px -apple-system, sans-serif";
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            for (var ti = 0; ti <= 5; ti++) {
                var leftV = gridMin + (gridMax - gridMin) * (ti / 5);
                var y = yOfGrid(leftV);
                for (var ri = 0; ri < rightAxisCfgs.length; ri++) {
                    var cfg = rightAxisCfgs[ri];
                    var rv = cfg.range.min + (cfg.range.max - cfg.range.min) * (ti / 5);
                    ctx.fillStyle = cfg.color;
                    ctx.fillText(fmtVal(rv), W - pad.r + 6, y + 4 + cfg.offsetY);
                }
            }
        }

        // ---- X axis labels ----
        ctx.fillStyle = _cc.label;
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        var labelStep = Math.max(1, Math.floor(nn / 10));
        for (var i = 0; i < nn; i += labelStep) {
            ctx.save();
            ctx.translate(xOf(i), H - pad.b + 8);
            ctx.rotate(0.35);
            ctx.fillText(labels[i], 0, 0);
            ctx.restore();
        }

        // ---- Draw lines ----
        for (var ci = 0; ci < visibleIndices.length; ci++) {
            var si = visibleIndices[ci];
            if (!seriesVisible[si]) continue;
            var meta = resourceMeta[si];
            var data = meta.data;

            ctx.lineWidth = __apLineW * 1.5;
            ctx.lineJoin = "round";
            ctx.strokeStyle = meta.color;
            ctx.beginPath();
            var started = false;
            for (var i = 0; i < data.length; i++) {
                if (data[i] === null || data[i] === undefined) {
                    started = false;
                    continue;
                }
                var x = xOf(i), y = yOfForMeta(meta, data[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else { ctx.lineTo(x, y); }
            }
            ctx.stroke();
        }

        // ---- Mouse interaction ----
        addListener(cv, "mousemove", function (e) {
            if (_isSelecting) return;
            var rect = cv.getBoundingClientRect();
            var mx_ = e.clientX - rect.left;
            var my_ = e.clientY - rect.top;

            oc = clearOverlay();

            if (mx_ < pad.l || mx_ > W - pad.r || my_ < pad.t || my_ > pad.t + gH) {
                tipEl.style.display = "none";
                return;
            }

            var state = chartState;
            var idx = Math.round(state.visibleStart + (mx_ - pad.l) / gW * Math.max(state.visibleNn - 1, 1));
            idx = Math.max(state.visibleStart, Math.min(state.visibleEnd - 1, idx));

            var px = state.xOf(idx);

            // Crosshair
            oc.strokeStyle = "rgba(255,255,255,0.18)";
            oc.lineWidth = 1;
            oc.setLineDash([4, 3]);
            oc.beginPath(); oc.moveTo(px, pad.t); oc.lineTo(px, pad.t + gH); oc.stroke();
            oc.setLineDash([]);

            // Build tooltip
            var tooltipRows = [
                { style: { color: "#888", marginBottom: "4px", fontWeight: "600" }, parts: [{ type: 'text', value: labels[idx] }] },
            ];

            for (var ci = 0; ci < state.visibleIndices.length; ci++) {
                var si = state.visibleIndices[ci];
                if (!seriesVisible[si]) continue;
                var meta = resourceMeta[si];
                var val = meta.data[idx];
                if (val === null || val === undefined) continue;
                tooltipRows.push({
                    parts: [
                        { type: 'text', value: meta.name + ": " },
                        { type: 'bold', value: fmtVal(val), style: { color: meta.color } }
                    ]
                });

                // Draw bead
                var by = state.yOfForMeta(meta, val);
                oc.beginPath(); oc.arc(px, by, 4, 0, Math.PI * 2);
                oc.fillStyle = meta.color;
                oc.fill();
                oc.strokeStyle = "#fff";
                oc.lineWidth = 1.5;
                oc.stroke();
            }

            setTooltipContent(tipEl, tooltipRows);
            tipEl.style.display = "block";
            var tx = px + 18;
            var ty = my_ - 60;
            if (tx + 200 > W) tx = px - 220;
            if (ty < 8) ty = my_ + 18;
            tipEl.style.left = tx + "px";
            tipEl.style.top = ty + "px";
        });

        addListener(cv, "mouseleave", function () {
            tipEl.style.display = "none";
            clearOverlay();
        });

        // ---- Legend toggle ----
        if (legendEl) {
            if (legendEl._legendHandler) {
                legendEl.removeEventListener("click", legendEl._legendHandler);
            }
            legendEl._legendHandler = function (e) {
                var item = e.target.closest(".rc-legend-item");
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
                legendEl.querySelectorAll(".rc-legend-item").forEach(function (li, i) {
                    var si = parseInt(li.getAttribute("data-series"), 10);
                    li.style.opacity = seriesVisible[si] ? "1" : "0.35";
                });
                renderZoomed();
            };
            addListener(legendEl, "click", legendEl._legendHandler);
            legendEl.querySelectorAll(".rc-legend-item").forEach(function (li, i) {
                var si = parseInt(li.getAttribute("data-series"), 10);
                li.style.opacity = seriesVisible[si] ? "1" : "0.35";
            });
        }

        // ---- Zoom/Pan ----
        zoomLevel = 1.0;
        panOffset = 0;
        setupZoomPan(ctx, oc);
    }

    function renderZoomed() {
        _cc = chartThemeColors();
        __apLineW = (_cc.lw === 'light') ? 2.3 : 1.9;
        resizeCanvas();
        // Redraw with zoom
        var visibleCount = clampPanForZoom();
        var visibleStart = Math.max(0, Math.floor(panOffset));
        var visibleEnd = Math.min(nn, visibleStart + visibleCount);
        var visibleNn = visibleEnd - visibleStart;

        var visibleIndices = getVisibleDataIndices();

        // ---- 所有资源独立 Y 轴范围（缩放后） ----
        var rightAxisCfgs = [];
        var yFns = {};
        var gridMin = Infinity, gridMax = -Infinity;
        (function () {
            for (var vi = 0; vi < visibleIndices.length; vi++) {
                var si = visibleIndices[vi];
                var meta = resourceMeta[si];
                if (yFns[meta.key]) continue;
                var mn = Infinity, mx = -Infinity;
                var data = meta.data;
                for (var i = visibleStart; i < visibleEnd && i < data.length; i++) {
                    if (data[i] === null || data[i] === undefined) continue;
                    if (data[i] < mn) mn = data[i];
                    if (data[i] > mx) mx = data[i];
                }
                if (mn === Infinity) { mn = 0; mx = 100; }
                if (mx === mn) { mx = mn + 100; }
                var rng = mx - mn;
                var extra = Math.max(rng * 0.2, mn * 0.2, mx * 0.1);
                mn -= extra;
                mx += extra;
                mn = Math.max(0, mn);
                if (mn < gridMin) gridMin = mn;
                if (mx > gridMax) gridMax = mx;
                (function (rMin, rMax, rKey) {
                    yFns[rKey] = function (v) {
                        return pad.t + gH - (v - rMin) / (rMax - rMin) * gH;
                    };
                })(mn, mx, meta.key);
                if (meta.side === 'right') {
                    rightAxisCfgs.push({ key: meta.key, color: meta.color, range: { min: mn, max: mx }, offsetY: (rightAxisCfgs.length) * 12 });
                }
            }
        })();
        if (gridMin === Infinity) { gridMin = 0; gridMax = 100; }
        var gridRng = gridMax - gridMin;
        var gridExtra = Math.max(gridRng * 0.1, gridMin * 0.08);
        gridMin -= gridExtra;
        gridMax += gridExtra;
        gridMin = Math.max(0, gridMin);

        var ctx = cv.getContext("2d");
        setCanvasTransform(ctx);

        function yOfGrid(v) { return pad.t + gH - (v - gridMin) / (gridMax - gridMin) * gH; }
        function yOfForMeta(meta, v) {
            var fn = yFns[meta.key];
            return fn ? fn(v) : pad.t;
        }
        function xOf(i) { return pad.l + ((i - visibleStart) / Math.max(visibleNn - 1, 1)) * gW; }

        chartState = {
            visibleStart: visibleStart,
            visibleEnd: visibleEnd,
            visibleNn: visibleNn,
            visibleIndices: visibleIndices,
            xOf: xOf,
            yOfForMeta: yOfForMeta
        };

        // Clear
        // 先复位变换再清屏，否则残留旧像素（重影）
        setCanvasTransform(ctx);
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = _cc.bg;
        ctx.fillRect(0, 0, W, H);

        // Grid & left axis 参考刻度
        ctx.strokeStyle = _cc.grid;
        ctx.lineWidth = 1;
        ctx.fillStyle = _cc.label;
        ctx.font = "11px -apple-system, sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (var i = 0; i <= 5; i++) {
            var v = gridMin + (gridMax - gridMin) * (i / 5);
            var y = yOfGrid(v);
            ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
            ctx.fillText(fmtVal(v), pad.l - 8, y);
        }

        // Right axis colored ticks
        if (rightAxisCfgs.length > 0) {
            ctx.font = "10px -apple-system, sans-serif";
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            for (var ti = 0; ti <= 5; ti++) {
                var leftV = gridMin + (gridMax - gridMin) * (ti / 5);
                var y = yOfGrid(leftV);
                for (var ri = 0; ri < rightAxisCfgs.length; ri++) {
                    var cfg = rightAxisCfgs[ri];
                    var rv = cfg.range.min + (cfg.range.max - cfg.range.min) * (ti / 5);
                    ctx.fillStyle = cfg.color;
                    ctx.fillText(fmtVal(rv), W - pad.r + 6, y + 4 + cfg.offsetY);
                }
            }
        }

        // X labels
        ctx.fillStyle = _cc.label;
        ctx.font = "10px -apple-system, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        var labelStep = Math.max(1, Math.floor(visibleNn / 10));
        for (var i = visibleStart; i < visibleEnd; i += labelStep) {
            ctx.save();
            ctx.translate(xOf(i), H - pad.b + 8);
            ctx.rotate(0.35);
            ctx.fillText(labels[i], 0, 0);
            ctx.restore();
        }

        // Lines
        for (var ci = 0; ci < visibleIndices.length; ci++) {
            var si = visibleIndices[ci];
            if (!seriesVisible[si]) continue;
            var meta = resourceMeta[si];
            var data = meta.data;

            ctx.lineWidth = __apLineW * 1.5;
            ctx.lineJoin = "round";
            ctx.strokeStyle = meta.color;
            ctx.beginPath();
            var started = false;
            for (var i = visibleStart; i < visibleEnd; i++) {
                if (data[i] === null || data[i] === undefined) { started = false; continue; }
                var x = xOf(i), y = yOfForMeta(meta, data[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else { ctx.lineTo(x, y); }
            }
            ctx.stroke();
        }
    }

    function setupZoomPan(ctx, oc) {
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

                oc = clearOverlay();

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
                renderZoomed();
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
                        zoomLevel = Math.min(maxZoom, nn / (endIdx - startIdx));
                        renderZoomed();
                    }
                }

                clearOverlay();
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
                renderZoomed();
            }
        }, { passive: false });

        addListener(cv, "dblclick", function () {
            zoomLevel = 1.0;
            panOffset = 0;
            renderZoomed();
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
                renderZoomed();
            });
        }
        if (zoomOutBtn) {
            addListener(zoomOutBtn, "click", function () {
                zoomLevel = Math.max(minZoom, zoomLevel / 1.5);
                renderZoomed();
            });
        }
        if (zoomResetBtn) {
            addListener(zoomResetBtn, "click", function () {
                zoomLevel = 1.0;
                panOffset = 0;
                renderZoomed();
            });
        }
    }

    function fmtVal(v) {
        if (v === null || v === undefined) return '-';
        if (typeof v === 'number') {
            if (Number.isInteger(v)) return v.toLocaleString();
            return v.toFixed(1);
        }
        return String(v);
    }
})();
