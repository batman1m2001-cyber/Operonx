// @ts-check

const vscode = acquireVsCodeApi();

let currentTraces = [];
let currentNodes = [];
let selectedNode = null;
let currentView = 'list';
let expandedNodes = new Set();
let selectedTags = new Set();
let allTags = [];
let searchQuery = '';
let currentTab = 'preview';
let leftPanelView = 'tree'; // 'tree' or 'timeline'
let splitPosition = 350; // pixels for left panel width
let isResizing = false;
let currentTimeFilter = undefined; // seconds ago, undefined = all
let currentPage = 1;
let totalTraces = 0;
const PAGE_SIZE = 50;

// Instant tooltip for data-tooltip elements
(function initTooltip() {
    const tip = document.createElement('div');
    tip.className = 'connect-tooltip';
    tip.style.display = 'none';
    document.body.appendChild(tip);

    document.addEventListener('mouseover', (e) => {
        const el = e.target.closest('[data-tooltip]');
        if (el) {
            tip.textContent = el.getAttribute('data-tooltip');
            const rect = el.getBoundingClientRect();
            tip.style.display = 'block';
            tip.style.left = (rect.right + 8) + 'px';
            tip.style.top = rect.top + 'px';
        }
    });
    document.addEventListener('mouseout', (e) => {
        const el = e.target.closest('[data-tooltip]');
        if (el) tip.style.display = 'none';
    });
    document.addEventListener('click', () => {
        tip.style.display = 'none';
    });
})();

// Request initial data
vscode.postMessage({ type: 'getTraceList' });
vscode.postMessage({ type: 'getDbInfo' });

// Handle messages from extension
window.addEventListener('message', event => {
    const message = event.data;
    switch (message.type) {
        case 'traceList':
            currentTraces = message.traces || [];
            totalTraces = message.total || 0;
            currentPage = message.page || 1;
            if (currentView === 'list') {
                renderTraceList(message.traces, message.error);
            }
            break;
        case 'traceDetail':
            currentNodes = message.nodes || [];
            renderTraceDetail(message.requestId, message.nodes, message.error);
            break;
        case 'dbInfo':
            renderDbInfo(message);
            break;
    }
});

function refresh() {
    fetchTraceList();
    vscode.postMessage({ type: 'getDbInfo' });
}

function fetchTraceList() {
    vscode.postMessage({ type: 'getTraceList', timeFilter: currentTimeFilter, page: currentPage });
}

function setTimeFilter(seconds) {
    currentTimeFilter = seconds || undefined;
    currentPage = 1;
    fetchTraceList();
}

function goToPage(page) {
    const totalPages = Math.ceil(totalTraces / PAGE_SIZE) || 1;
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    fetchTraceList();
}

function clearTraces() {
    vscode.postMessage({ type: 'clearTraces' });
}

function renderDbInfo(info) {
    const el = document.getElementById('db-info');
    if (!el) return;
    const sizeKb = (info.size / 1024).toFixed(1);
    el.textContent = `Database: ${info.path} (${info.exists ? sizeKb + ' KB' : 'not found'})`;
}

function renderTraceList(traces, error) {
    currentView = 'list';
    const listEl = document.getElementById('trace-list');
    const detailEl = document.getElementById('trace-detail');

    if (!listEl || !detailEl) return;

    listEl.style.display = 'block';
    detailEl.style.display = 'none';

    if (error) {
        listEl.innerHTML = `<div class="error-state">Error: ${escapeHtml(error)}</div>`;
        return;
    }

    if (!traces) traces = [];

    // Collect all unique tags
    allTags = collectAllTags(traces);

    // Filter traces by selected tags
    let filteredTraces = traces;
    if (selectedTags.size > 0) {
        filteredTraces = traces.filter(trace => {
            if (!trace.tags || trace.tags.length === 0) return false;
            return Array.from(selectedTags).every(tag => trace.tags.includes(tag));
        });
    }

    // Filter by search query
    if (searchQuery) {
        const query = searchQuery.toLowerCase();
        filteredTraces = filteredTraces.filter(trace => {
            const workflowMatch = trace.workflow_name && trace.workflow_name.toLowerCase().includes(query);
            const tagMatch = trace.tags && trace.tags.some(t => t && t.toLowerCase().includes(query));
            const inputMatch = trace.input_preview && trace.input_preview.toLowerCase().includes(query);
            const outputMatch = trace.output_preview && trace.output_preview.toLowerCase().includes(query);
            return workflowMatch || tagMatch || inputMatch || outputMatch;
        });
    }

    // Render time filter buttons
    const timeFilters = [
        { label: '1h', value: 3600 },
        { label: '24h', value: 86400 },
        { label: '7d', value: 604800 },
        { label: '30d', value: 2592000 },
        { label: 'All', value: undefined },
    ];
    const timeFilterHtml = `
        <div class="time-filter-container">
            ${timeFilters.map(f => `<button class="time-filter-btn ${currentTimeFilter === f.value ? 'active' : ''}" onclick="setTimeFilter(${f.value})">${f.label}</button>`).join('')}
        </div>
    `;

    // Render search bar
    let searchHtml = `
        <div class="search-container">
            <input type="text"
                   class="search-input"
                   placeholder="Search traces by name, tags, input, output..."
                   value="${escapeHtml(searchQuery)}"
                   oninput="handleSearch(this.value)" />
            ${searchQuery ? `<button class="search-clear" onclick="clearSearch()">×</button>` : ''}
        </div>
    `;

    // Render tag filter
    let filterHtml = '';
    if (allTags.length > 0) {
        const tagButtons = allTags.map(tag => {
            const isSelected = selectedTags.has(tag);
            return `<button class="tag-filter ${isSelected ? 'selected' : ''}" onclick="toggleTagFilter('${escapeHtml(tag)}')">${escapeHtml(tag)}</button>`;
        }).join('');
        filterHtml = `
            <div class="tag-filter-container">
                <span class="filter-label">Filter by tags:</span>
                ${tagButtons}
                ${selectedTags.size > 0 ? `<button class="tag-filter clear" onclick="clearTagFilter()">Clear</button>` : ''}
            </div>
        `;
    }

    let html = timeFilterHtml + searchHtml + filterHtml + `
        <table class="trace-table">
            <thead>
                <tr>
                    <th>Workflow</th>
                    <th>Input</th>
                    <th>Output</th>
                    <th>Tags</th>
                    <th>Latency</th>
                    <th>Tokens</th>
                    <th>Cost</th>
                </tr>
            </thead>
            <tbody>
    `;

    for (const trace of filteredTraces) {
        const duration = trace.total_duration_ms ? formatDuration(trace.total_duration_ms) : '-';
        const promptTokens = trace.prompt_tokens || 0;
        const completionTokens = trace.completion_tokens || 0;
        const totalTokens = trace.total_tokens || 0;
        const tokensDisplay = totalTokens ? `<span class="tokens-breakdown">${promptTokens} → ${completionTokens}</span> <span class="tokens-total">(Σ ${totalTokens})</span>` : '-';
        const cost = trace.total_cost ? '$' + trace.total_cost.toFixed(4) : '-';
        const tagsHtml = (trace.tags || [])
            .filter(t => t != null)
            .slice(0, 2)
            .map((t, i) => `<span class="tag small ${getTagColorClass(t)}">${escapeHtml(t)}</span>`)
            .join('') + (trace.tags && trace.tags.length > 2 ? `<span class="tag-more">+${trace.tags.length - 2}</span>` : '');

        // Input/Output preview
        const inputPreview = trace.input_preview ? truncateText(trace.input_preview, 40) : '-';
        const outputPreview = trace.output_preview ? truncateText(trace.output_preview, 40) : '-';

        html += `
            <tr onclick="showTrace('${escapeHtml(trace.request_id)}')">
                <td class="workflow">
                    <div class="workflow-name">${escapeHtml(trace.workflow_name)}</div>
                    <div class="workflow-time">${trace.start_time ? formatTimeShort(trace.start_time) : ''}</div>
                </td>
                <td class="io-preview">${escapeHtml(inputPreview)}</td>
                <td class="io-preview">${escapeHtml(outputPreview)}</td>
                <td class="tags-cell">${tagsHtml}</td>
                <td class="duration">${duration}</td>
                <td class="tokens">${tokensDisplay}</td>
                <td class="cost">${cost}</td>
            </tr>
        `;
    }

    html += '</tbody></table>';

    if (filteredTraces.length === 0) {
        if (traces.length === 0) {
            html += '<div class="empty-state">No traces found.<br><br>Run a workflow with a tracer to see traces here.</div>';
        } else {
            html += '<div class="empty-state">No traces match your filters.</div>';
        }
    }

    // Pagination
    const totalPages = Math.ceil(totalTraces / PAGE_SIZE) || 1;
    if (totalTraces > 0) {
        const from = (currentPage - 1) * PAGE_SIZE + 1;
        const to = Math.min(currentPage * PAGE_SIZE, totalTraces);
        let paginationHtml = `<div class="pagination">`;
        paginationHtml += `<span class="page-info">${from}–${to} of ${totalTraces}</span>`;
        paginationHtml += `<button class="page-btn" onclick="goToPage(1)" ${currentPage === 1 ? 'disabled' : ''}>«</button>`;
        paginationHtml += `<button class="page-btn" onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>‹</button>`;
        paginationHtml += `<span class="page-num">Page ${currentPage} / ${totalPages}</span>`;
        paginationHtml += `<button class="page-btn" onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>›</button>`;
        paginationHtml += `<button class="page-btn" onclick="goToPage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''}>»</button>`;
        paginationHtml += `</div>`;
        html += paginationHtml;
    }

    listEl.innerHTML = html;
}

function handleSearch(query) {
    searchQuery = query;
    renderTraceList(currentTraces, null);
    const input = document.querySelector('.search-input');
    if (input) {
        input.focus();
        input.setSelectionRange(query.length, query.length);
    }
}

function clearSearch() {
    searchQuery = '';
    renderTraceList(currentTraces, null);
}

function truncateText(text, maxLen) {
    if (!text) return '';
    const str = String(text);
    if (str.length <= maxLen) return str;
    return str.substring(0, maxLen) + '...';
}

function formatTimeShort(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return '';
    }
}

function getTagColorClass(tag) {
    if (!tag) return '';
    const colors = ['blue', 'green', 'purple', 'yellow', 'cyan'];
    const hash = tag.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
    return colors[hash % colors.length];
}

function collectAllTags(traces) {
    const tagSet = new Set();
    for (const trace of traces) {
        if (trace.tags && Array.isArray(trace.tags)) {
            for (const tag of trace.tags) {
                if (tag != null) tagSet.add(tag);
            }
        }
    }
    return Array.from(tagSet).sort();
}

function toggleTagFilter(tag) {
    if (selectedTags.has(tag)) {
        selectedTags.delete(tag);
    } else {
        selectedTags.add(tag);
    }
    renderTraceList(currentTraces, null);
}

function clearTagFilter() {
    selectedTags.clear();
    renderTraceList(currentTraces, null);
}

function showTrace(requestId) {
    vscode.postMessage({ type: 'getTraceDetail', requestId });
}

function renderTraceDetail(requestId, nodes, error) {
    currentView = 'detail';
    currentNodes = nodes || [];
    expandedNodes = new Set();
    currentTab = 'preview';

    const listEl = document.getElementById('trace-list');
    const detailEl = document.getElementById('trace-detail');

    if (!listEl || !detailEl) return;

    listEl.style.display = 'none';
    detailEl.style.display = 'grid';
    detailEl.style.gridTemplateColumns = `${splitPosition}px 4px 1fr`;

    if (error) {
        detailEl.innerHTML = `<div class="error-state">Error: ${escapeHtml(error)}</div>`;
        return;
    }

    const trace = currentTraces.find(t => t.request_id === requestId);

    // Expand all nodes by default
    expandAllNodes(currentNodes);

    // Calculate total duration for timeline
    const totalDuration = trace ? trace.total_duration_ms : 0;

    // Generate tree/timeline content
    const treeContent = leftPanelView === 'tree'
        ? `<div class="tree-root">${renderTree(currentNodes)}</div>`
        : leftPanelView === 'graph'
        ? renderGraph(currentNodes)
        : renderTimeline(currentNodes, totalDuration);

    let html = `
        <div id="tree-panel">
            <div class="tree-header">
                <span class="tree-title">Trace</span>
                <div class="view-toggle">
                    <button class="view-btn ${leftPanelView === 'tree' ? 'active' : ''}" onclick="switchLeftView('tree')" title="Tree View">
                        <span class="view-icon">⊞</span>
                    </button>
                    <button class="view-btn ${leftPanelView === 'timeline' ? 'active' : ''}" onclick="switchLeftView('timeline')" title="Timeline View">
                        <span class="view-icon">▬</span>
                    </button>
                    <button class="view-btn ${leftPanelView === 'graph' ? 'active' : ''}" onclick="switchLeftView('graph')" title="Graph View">
                        <span class="view-icon">⬡</span>
                    </button>
                </div>
            </div>
            <div class="tree-content">
                ${treeContent}
            </div>
        </div>
        <div id="split-bar" onmousedown="startResize(event)"></div>
        <div id="node-panel">
            <div class="empty-state">Select a node to view details</div>
        </div>
    `;

    detailEl.innerHTML = html;

    // Select first node by default
    if (nodes.length > 0) {
        selectNode(nodes[0]);
    }
}

function switchLeftView(view) {
    leftPanelView = view;
    // Re-render the tree/timeline content
    const treePanel = document.getElementById('tree-panel');
    if (!treePanel) return;

    const trace = currentTraces.find(t => t.request_id === currentNodes[0]?.request_id);
    const totalDuration = trace ? trace.total_duration_ms : (currentNodes[0]?.duration_ms || 1000);

    const header = treePanel.querySelector('.tree-header');
    const headerHtml = header ? header.outerHTML : '';

    treePanel.innerHTML = `
        <div class="tree-header">
            <span class="tree-title">Trace</span>
            <div class="view-toggle">
                <button class="view-btn ${leftPanelView === 'tree' ? 'active' : ''}" onclick="switchLeftView('tree')" title="Tree View">
                    <span class="view-icon">⊞</span>
                </button>
                <button class="view-btn ${leftPanelView === 'timeline' ? 'active' : ''}" onclick="switchLeftView('timeline')" title="Timeline View">
                    <span class="view-icon">▬</span>
                </button>
                <button class="view-btn ${leftPanelView === 'graph' ? 'active' : ''}" onclick="switchLeftView('graph')" title="Graph View">
                    <span class="view-icon">⬡</span>
                </button>
            </div>
        </div>
        <div class="tree-content">
            ${leftPanelView === 'tree' ? `<div class="tree-root">${renderTree(currentNodes)}</div>` : leftPanelView === 'graph' ? renderGraph(currentNodes) : renderTimeline(currentNodes, totalDuration)}
        </div>
    `;

    // Re-apply selection
    if (selectedNode) {
        const el = document.querySelector(`.tree-item[data-id="${selectedNode.id}"], .timeline-row[data-id="${selectedNode.id}"]`);
        if (el) {
            el.classList.add('selected');
        }
    }
}

// Calculate pixels per ms based on total duration
// Returns scale factor: how many pixels per millisecond
function getTimelineScale(durationMs) {
    const SECOND = 1000;
    const MINUTE = 60 * SECOND;
    const HOUR = 60 * MINUTE;
    const DAY = 24 * HOUR;

    // Dynamic scale: px per ms (compact view)
    // Target: 1s ≈ 50px, scales down for longer traces
    if (durationMs <= 500) return 0.15;                   // <= 500ms: 0.15px/ms (500ms = 75px)
    if (durationMs <= SECOND) return 0.08;                // <= 1s: 0.08px/ms (1s = 80px)
    if (durationMs <= 5 * SECOND) return 0.05;            // <= 5s: 0.05px/ms (5s = 250px)
    if (durationMs <= 30 * SECOND) return 0.02;           // <= 30s: 0.02px/ms (30s = 600px)
    if (durationMs <= MINUTE) return 0.012;               // <= 1min: 0.012px/ms (1min = 720px)
    if (durationMs <= 5 * MINUTE) return 0.004;           // <= 5min: 0.004px/ms (5min = 1200px)
    if (durationMs <= 15 * MINUTE) return 0.0018;         // <= 15min: 0.0018px/ms (15min = 1620px)
    if (durationMs <= HOUR) return 0.0006;                // <= 1h: 0.0006px/ms (1h = 2160px)
    if (durationMs <= 6 * HOUR) return 0.00012;           // <= 6h: 0.00012px/ms (6h = 2592px)
    if (durationMs <= DAY) return 0.000035;               // <= 1day: 0.000035px/ms (1day = 3024px)
    return 0.000015;                                      // > 1day: 0.000015px/ms
}

function calculateTrackWidth(durationMs) {
    const scale = getTimelineScale(durationMs);
    const width = durationMs * scale;
    // Min 80px, Max 3500px
    return Math.min(Math.max(width, 80), 3500);
}

function renderTimeline(nodes, totalDuration) {
    if (!nodes || !Array.isArray(nodes) || nodes.length === 0) {
        return '<div class="empty-tree">No nodes to display</div>';
    }

    // Find the earliest start time and latest end time from all nodes
    const allNodes = flattenNodes(nodes);
    let minTime = Infinity;
    let maxTime = -Infinity;

    for (const node of allNodes) {
        if (node.start_time) {
            const start = new Date(node.start_time).getTime();
            if (!isNaN(start)) {
                minTime = Math.min(minTime, start);
            }
        }
        if (node.end_time) {
            const end = new Date(node.end_time).getTime();
            if (!isNaN(end)) {
                maxTime = Math.max(maxTime, end);
            }
        }
    }

    // Fallback if no valid times
    if (minTime === Infinity || maxTime === -Infinity) {
        minTime = 0;
        maxTime = totalDuration || 1000;
    }

    const timeRange = maxTime - minTime;
    const effectiveRange = timeRange > 0 ? timeRange : 1000;

    // Calculate fixed width based on duration
    const trackWidth = calculateTrackWidth(effectiveRange);

    // Generate time ruler
    const rulerHtml = renderTimeRuler(effectiveRange, trackWidth);

    // Collect tracks (flat) while rendering labels (nested)
    const tracks = [];
    const labelsHtml = renderTimelineNodes(nodes, minTime, effectiveRange, trackWidth, tracks);

    // Render flat tracks overlay — all aligned at same left edge
    const ROW_HEIGHT = 28;
    const tracksHtml = tracks.map((t, i) =>
        `<div class="timeline-track-abs" data-id="${t.id}" style="top: ${i * ROW_HEIGHT + 4}px; width: ${trackWidth}px" onclick="selectNodeById(${t.id})">
            <div class="timeline-bar ${t.nodeType}" style="left: ${t.leftPx}px; width: ${t.widthPx}px">
                <span class="bar-duration">${t.durationText}</span>
            </div>
        </div>`
    ).join('');

    const containerHeight = tracks.length * ROW_HEIGHT;

    let html = '<div class="timeline-wrapper">';
    html += rulerHtml;
    html += `<div class="timeline-container" style="min-height: ${containerHeight}px">`;
    html += `<div class="timeline-labels">${labelsHtml}</div>`;
    html += `<div class="timeline-tracks">${tracksHtml}</div>`;
    html += '</div></div>';
    return html;
}

function flattenNodes(nodes) {
    let result = [];
    for (const node of nodes) {
        result.push(node);
        if (node.children && node.children.length > 0) {
            result = result.concat(flattenNodes(node.children));
        }
    }
    return result;
}

// ==================== Graph View ====================

const GRAPH_NODE_W = 180;
const GRAPH_NODE_H = 48;
const GRAPH_H_GAP = 80;
const GRAPH_V_GAP = 20;
const GRAPH_PAD = 24;

/**
 * Layout tree nodes into a hierarchical left-to-right graph.
 * Returns Map<nodeId, {x, y, node}> and overall {width, height}.
 */
function layoutGraph(roots) {
    const positions = new Map();

    // Recursive: compute subtree height and assign positions
    function computeSubtree(node, depth) {
        const childLayouts = [];
        if (node.children && node.children.length > 0) {
            for (const child of node.children) {
                childLayouts.push(computeSubtree(child, depth + 1));
            }
        }
        const selfH = GRAPH_NODE_H;
        let subtreeH;
        if (childLayouts.length === 0) {
            subtreeH = selfH;
        } else {
            subtreeH = childLayouts.reduce((sum, c) => sum + c.height, 0) + (childLayouts.length - 1) * GRAPH_V_GAP;
            subtreeH = Math.max(subtreeH, selfH);
        }
        return { node, depth, selfH, subtreeH: subtreeH, childLayouts };
    }

    function assignPositions(layout, x, yStart) {
        const { node, depth, subtreeH, childLayouts } = layout;
        const nodeX = x;
        const nodeCenterY = yStart + subtreeH / 2;
        const nodeY = nodeCenterY - GRAPH_NODE_H / 2;
        positions.set(node.id, { x: nodeX, y: nodeY, node });

        if (childLayouts.length > 0) {
            const childX = x + GRAPH_NODE_W + GRAPH_H_GAP;
            const totalChildH = childLayouts.reduce((sum, c) => sum + c.subtreeH, 0) + (childLayouts.length - 1) * GRAPH_V_GAP;
            let childY = nodeCenterY - totalChildH / 2;
            for (const cl of childLayouts) {
                assignPositions(cl, childX, childY);
                childY += cl.subtreeH + GRAPH_V_GAP;
            }
        }
    }

    let totalH = 0;
    const rootLayouts = roots.map(r => computeSubtree(r, 0));
    const totalRootH = rootLayouts.reduce((s, l) => s + l.subtreeH, 0) + Math.max(0, rootLayouts.length - 1) * GRAPH_V_GAP;

    let yOffset = GRAPH_PAD;
    for (const rl of rootLayouts) {
        assignPositions(rl, GRAPH_PAD, yOffset);
        yOffset += rl.subtreeH + GRAPH_V_GAP;
    }

    // Compute canvas size
    let maxX = 0, maxY = 0;
    for (const pos of positions.values()) {
        maxX = Math.max(maxX, pos.x + GRAPH_NODE_W);
        maxY = Math.max(maxY, pos.y + GRAPH_NODE_H);
    }

    return { positions, width: maxX + GRAPH_PAD, height: maxY + GRAPH_PAD };
}

function getEdgeColor(nodeType) {
    const colors = {
        llm: 'rgba(163,113,247,0.6)', embedding: 'rgba(163,113,247,0.5)', rerank: 'rgba(163,113,247,0.5)',
        branch: 'rgba(210,153,34,0.6)', 'for': 'rgba(219,109,40,0.6)', map: 'rgba(219,109,40,0.6)',
        'while': 'rgba(245,158,11,0.6)', stream: 'rgba(34,211,238,0.6)',
        code: 'rgba(63,185,80,0.6)', lambda: 'rgba(63,185,80,0.5)', parser: 'rgba(34,211,238,0.5)',
        prompt: 'rgba(219,97,162,0.6)', 'doc-processor': 'rgba(88,166,255,0.6)',
        milvus: 'rgba(88,166,255,0.5)', mongo: 'rgba(63,185,80,0.5)', s3: 'rgba(219,109,40,0.5)',
        graph: 'rgba(34,211,238,0.6)', data: 'rgba(88,166,255,0.5)',
        mcp: 'rgba(248,81,73,0.5)', 'tool-executor': 'rgba(139,148,158,0.5)',
        iteration: 'rgba(88,166,255,0.4)', default: 'rgba(139,148,158,0.4)'
    };
    return colors[nodeType] || colors.default;
}

function renderGraphEdges(roots, positions) {
    let paths = '';
    function walk(node) {
        const parentPos = positions.get(node.id);
        if (!parentPos) return;
        if (node.children) {
            for (const child of node.children) {
                const childPos = positions.get(child.id);
                if (!childPos) continue;
                const x1 = parentPos.x + GRAPH_NODE_W;
                const y1 = parentPos.y + GRAPH_NODE_H / 2;
                const x2 = childPos.x;
                const y2 = childPos.y + GRAPH_NODE_H / 2;
                const cpx = (x1 + x2) / 2;
                const edgeColor = getEdgeColor(getNodeType(node));
                paths += `<path d="M${x1},${y1} C${cpx},${y1} ${cpx},${y2} ${x2},${y2}" fill="none" stroke="${edgeColor}" stroke-width="2"/>`;
                // Arrowhead
                paths += `<polygon points="${x2},${y2} ${x2 - 6},${y2 - 4} ${x2 - 6},${y2 + 4}" fill="${edgeColor}"/>`;
                walk(child);
            }
        }
    }
    for (const root of roots) walk(root);
    return paths;
}

function renderGraph(nodes) {
    if (!nodes || !Array.isArray(nodes) || nodes.length === 0) {
        return '<div class="empty-tree">No nodes to display</div>';
    }

    const { positions, width, height } = layoutGraph(nodes);

    // Build SVG edges
    const edgesSvg = renderGraphEdges(nodes, positions);

    // Build node cards
    let nodesHtml = '';
    for (const [id, pos] of positions) {
        const node = pos.node;
        const nodeType = getNodeType(node);
        const shortName = getShortName(node.node_name);
        const icon = getNodeIcon(node);
        const duration = node.duration_ms ? formatDuration(node.duration_ms) : '';
        const isSelected = selectedNode && selectedNode.id === id;

        nodesHtml += `
            <div class="graph-node ${nodeType}${isSelected ? ' selected' : ''}" data-id="${id}"
                 style="left:${pos.x}px; top:${pos.y}px; width:${GRAPH_NODE_W}px; height:${GRAPH_NODE_H}px"
                 onclick="selectNodeById(${id})">
                <span class="graph-node-icon ${nodeType}">${icon}</span>
                <span class="graph-node-name">${escapeHtml(shortName)}</span>
                ${duration ? `<span class="graph-node-badge">${duration}</span>` : ''}
            </div>
        `;
    }

    return `
        <div class="graph-wrapper">
            <div class="graph-canvas" style="width:${width}px; height:${height}px; position:relative;">
                <svg class="graph-svg" width="${width}" height="${height}">${edgesSvg}</svg>
                ${nodesHtml}
            </div>
        </div>
    `;
}

function renderTimeRuler(totalMs, trackWidth) {
    // Determine appropriate tick intervals
    let tickInterval;

    if (totalMs <= 100) {
        tickInterval = 20;
    } else if (totalMs <= 500) {
        tickInterval = 100;
    } else if (totalMs <= 1000) {
        tickInterval = 200;
    } else if (totalMs <= 5000) {
        tickInterval = 1000;
    } else if (totalMs <= 30000) {
        tickInterval = 5000;
    } else {
        tickInterval = 10000;
    }

    const tickCount = Math.ceil(totalMs / tickInterval) + 1;

    let html = `<div class="timeline-ruler" style="width: ${trackWidth}px">`;
    for (let i = 0; i < tickCount; i++) {
        const ms = i * tickInterval;
        const leftPx = (ms / totalMs) * trackWidth;
        if (leftPx > trackWidth) break;

        const label = ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
        html += `<div class="ruler-tick" style="left: ${leftPx}px"><span class="ruler-label">${label}</span></div>`;
    }
    html += '</div>';
    return html;
}

function renderTimelineNodes(nodes, minTime, totalRange, trackWidth, tracks) {
    const chevronDown = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const chevronRight = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    let html = '';
    for (const node of nodes) {
        const nodeType = getNodeType(node);
        const hasChildren = node.children && node.children.length > 0;
        const isExpanded = expandedNodes.has(node.id);

        // Calculate position and width in pixels based on actual times
        let leftPx = 0;
        let widthPx = 6; // minimum width

        if (node.start_time) {
            const startMs = new Date(node.start_time).getTime();
            if (!isNaN(startMs)) {
                leftPx = ((startMs - minTime) / totalRange) * trackWidth;
            }
        }

        if (node.duration_ms && totalRange > 0) {
            widthPx = Math.max((node.duration_ms / totalRange) * trackWidth, 6);
        } else if (node.start_time && node.end_time) {
            const startMs = new Date(node.start_time).getTime();
            const endMs = new Date(node.end_time).getTime();
            if (!isNaN(startMs) && !isNaN(endMs)) {
                widthPx = Math.max(((endMs - startMs) / totalRange) * trackWidth, 6);
            }
        }

        // Ensure bar doesn't overflow
        if (leftPx + widthPx > trackWidth) {
            widthPx = trackWidth - leftPx;
        }

        // Only show duration text if bar is wide enough
        const durationText = (node.duration_ms && widthPx >= 50) ? formatDuration(node.duration_ms) : '';

        // Collect track data for flat overlay
        tracks.push({ id: node.id, nodeType, leftPx, widthPx, durationText, trackWidth });

        // Render label tree (nested structure for connectors)
        html += `
            <div class="tree-node">
                <div class="timeline-row" data-id="${node.id}" onclick="selectNodeById(${node.id})">
                    <div class="timeline-label">
                        <span class="tree-icon ${nodeType}">${getNodeIcon(node)}</span>
                        <span class="timeline-name">${escapeHtml(getShortName(node.node_name))}</span>
                    </div>
                    ${hasChildren ? `<span class="tree-toggle has-children" onclick="event.stopPropagation(); toggleNode(${node.id})">${isExpanded ? chevronDown : chevronRight}</span>` : '<span class="tree-toggle"></span>'}
                </div>
        `;

        if (hasChildren && isExpanded) {
            html += `<div class="tree-children">`;
            html += renderTimelineNodes(node.children, minTime, totalRange, trackWidth, tracks);
            html += '</div>';
        }

        html += '</div>';
    }
    return html;
}

// Resize functionality
function startResize(e) {
    isResizing = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    document.addEventListener('mousemove', doResize);
    document.addEventListener('mouseup', stopResize);
}

function doResize(e) {
    if (!isResizing) return;

    const detailEl = document.getElementById('trace-detail');
    if (!detailEl) return;

    const rect = detailEl.getBoundingClientRect();
    let newWidth = e.clientX - rect.left;

    // Constrain width
    newWidth = Math.max(200, Math.min(newWidth, rect.width - 300));

    splitPosition = newWidth;
    detailEl.style.gridTemplateColumns = `${splitPosition}px 4px 1fr`;
}

function stopResize() {
    isResizing = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';

    document.removeEventListener('mousemove', doResize);
    document.removeEventListener('mouseup', stopResize);
}

function expandAllNodes(nodes) {
    if (!nodes || !Array.isArray(nodes)) return;
    for (const node of nodes) {
        if (node.children && node.children.length > 0) {
            expandedNodes.add(node.id);
            expandAllNodes(node.children);
        }
    }
}

function renderTree(nodes) {
    if (!nodes || !Array.isArray(nodes) || nodes.length === 0) {
        return '<div class="empty-tree">No nodes to display</div>';
    }
    let html = '';
    for (const node of nodes) {
        html += renderTreeNode(node);
    }
    return html;
}

function renderTreeNode(node, isLast = false) {
    const hasChildren = node.children && node.children.length > 0;
    const isExpanded = expandedNodes.has(node.id);
    const nodeType = getNodeType(node);
    const duration = node.duration_ms ? formatDuration(node.duration_ms) : '';
    const tokens = node.total_tokens ? `${node.total_tokens} tok` : '';

    const chevronDown = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const chevronRight = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4.5 3L7.5 6L4.5 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    let html = `
        <div class="tree-node">
            <div class="tree-item ${nodeType}" data-id="${node.id}" onclick="selectNodeById(${node.id})">
                <span class="tree-icon ${nodeType}">${getNodeIcon(node)}</span>
                <span class="tree-node-content">
                    <span class="tree-name">${escapeHtml(getShortName(node.node_name))}</span>
                </span>
                ${duration ? `<span class="tree-duration">${duration}</span>` : ''}
                ${hasChildren ? `<span class="tree-toggle has-children" onclick="event.stopPropagation(); toggleNode(${node.id})">${isExpanded ? chevronDown : chevronRight}</span>` : '<span class="tree-toggle"></span>'}
            </div>
    `;

    if (hasChildren) {
        html += `<div class="tree-children" style="display: ${isExpanded ? 'block' : 'none'}">`;
        const childCount = node.children.length;
        for (let i = 0; i < childCount; i++) {
            html += renderTreeNode(node.children[i], i === childCount - 1);
        }
        html += '</div>';
    }

    html += '</div>';
    return html;
}

function getNodeType(node) {
    // Use actual node_type from database if available
    if (node.node_type) {
        return node.node_type;
    }
    // Fallback to metadata.type
    if (node.metadata && node.metadata.type) {
        return node.metadata.type;
    }
    // Fallback to inference for legacy data
    if (node.contain_generation) return 'llm';
    if (node.node_name && node.node_name.includes('iteration[')) return 'iteration';
    if (node.node_name && (node.node_name.includes('_loop') || node.node_name.includes('map_'))) return 'for';
    if (node.children && node.children.length > 0) return 'graph';
    return 'default';
}

function toggleNode(id) {
    if (expandedNodes.has(id)) {
        expandedNodes.delete(id);
    } else {
        expandedNodes.add(id);
    }

    // Re-render the current view
    const treeContent = document.querySelector('.tree-content');
    if (treeContent) {
        const trace = currentTraces.find(t => t.request_id === currentNodes[0]?.request_id);
        const totalDuration = trace ? trace.total_duration_ms : (currentNodes[0]?.duration_ms || 1000);
        treeContent.innerHTML = leftPanelView === 'tree'
            ? `<div class="tree-root">${renderTree(currentNodes)}</div>`
            : leftPanelView === 'graph'
            ? renderGraph(currentNodes)
            : renderTimeline(currentNodes, totalDuration);

        // Re-apply selection
        if (selectedNode) {
            const el = document.querySelector(`.tree-item[data-id="${selectedNode.id}"], .timeline-row[data-id="${selectedNode.id}"], .graph-node[data-id="${selectedNode.id}"]`);
            if (el) {
                el.classList.add('selected');
            }
        }
    }
}

function getNodeIcon(node) {
    const nodeType = node.node_type || getNodeType(node);

    // Icons/symbols for each node type
    const icons = {
        // AI/ML nodes
        'llm': '🧠',           // Brain for AI/LLM
        'embedding': '◈',     // Diamond with dot
        'rerank': '⇅',        // Reordering arrows

        // Flow control nodes
        'branch': '⑂',        // OCR Branch symbol
        'for': '↻',           // Loop arrow
        'map': '⊶',           // Parallel map
        'while': '↺',         // Repeat arrow
        'stream': '≋',        // Stream waves

        // Transform nodes
        'code': 'ƒ',          // Function symbol
        'lambda': 'λ',        // Lambda
        'parser': '⟨⟩',       // Angle brackets
        'prompt': '✎',        // Pencil
        'doc-processor': '📄', // Document

        // Database/storage nodes
        'milvus': '⬡',        // Hexagon (vector db)
        'mongo': '🍃',         // MongoDB leaf
        's3': '☁',           // Cloud

        // Special nodes
        'graph': null,         // Uses hush logo image
        'data': '⬤',          // Data circle
        'default': '○',       // Default circle
        'dummy': '◌',         // Dashed circle
        'tool-executor': '⚙', // Gear
        'mcp': '⧉',          // Two joined squares (MCP)
        'iteration': '⟳',     // Iteration cycle
    };

    if (nodeType === 'graph' && window.hushIcons) {
        return `<img src="${window.hushIcons.icon16}" class="hush-logo-icon" alt="graph" />`;
    }
    return icons[nodeType] || '•';
}

function getShortName(name) {
    if (!name) return 'unknown';

    // For iteration nodes like iteration[0][1][2], just show the last [N]
    if (name.includes('iteration[')) {
        const match = name.match(/\[(\d+)\](?!.*\[)/);
        if (match) {
            return `[${match[1]}]`;
        }
    }

    // Get everything after the last dot
    const lastDot = name.lastIndexOf('.');
    if (lastDot === -1) return name;
    return name.substring(lastDot + 1);
}

function selectNodeById(id) {
    const node = findNodeById(currentNodes, id);
    if (node) {
        selectNode(node);
    }
}

function findNodeById(nodes, id) {
    for (const node of nodes) {
        if (node.id === id) return node;
        if (node.children) {
            const found = findNodeById(node.children, id);
            if (found) return found;
        }
    }
    return null;
}

function selectNode(node, highlightKey) {
    selectedNode = node;

    // Update selection UI for tree, timeline, and graph views
    document.querySelectorAll('.tree-item, .timeline-row, .timeline-track-abs, .graph-node').forEach(el => {
        el.classList.remove('selected');
    });
    const treeEl = document.querySelector(`.tree-item[data-id="${node.id}"]`);
    const timelineRowEl = document.querySelector(`.timeline-row[data-id="${node.id}"]`);
    const timelineTrackEl = document.querySelector(`.timeline-track-abs[data-id="${node.id}"]`);
    const graphEl = document.querySelector(`.graph-node[data-id="${node.id}"]`);
    if (treeEl) {
        treeEl.classList.add('selected');
    }
    if (timelineRowEl) {
        timelineRowEl.classList.add('selected');
    }
    if (timelineTrackEl) {
        timelineTrackEl.classList.add('selected');
    }
    if (graphEl) {
        graphEl.classList.add('selected');
    }

    renderNodeDetail(node);

    // Highlight the specific variable key if provided
    if (highlightKey) {
        const panel = document.getElementById('node-panel');
        if (panel) {
            const allKeys = panel.querySelectorAll('.kv-key');
            for (const keyEl of allKeys) {
                if (keyEl.textContent.trim() === highlightKey) {
                    keyEl.classList.add('highlighted');
                    keyEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    // Remove highlight after animation
                    setTimeout(() => keyEl.classList.remove('highlighted'), 500);
                    break;
                }
            }
        }
    }
}

function renderNodeDetail(node) {
    const panel = document.getElementById('node-panel');
    if (!panel) return;

    const nodeType = getNodeType(node);
    const duration = node.duration_ms ? formatDuration(node.duration_ms) : '-';
    const model = node.model || '-';
    const promptTokens = node.prompt_tokens || 0;
    const completionTokens = node.completion_tokens || 0;
    const totalTokens = node.total_tokens || 0;
    const tokensDisplay = totalTokens ? `${promptTokens} → ${completionTokens} (Σ ${totalTokens})` : '-';
    const cost = node.cost_usd ? '$' + node.cost_usd.toFixed(4) : '-';

    let tagsHtml = '';
    if (node.tags && node.tags.length > 0) {
        const tags = node.tags
            .filter(t => t != null)
            .map(t => `<span class="tag ${getTagColorClass(t)}">${escapeHtml(t)}</span>`)
            .join('');
        tagsHtml = `<div class="tags-container">${tags}</div>`;
    }

    const traceId = node.request_id || '-';
    const sessionId = node.session_id || '';

    let html = `
        <div class="node-header">
            <button class="detail-close-btn" onclick="backToList()" title="Close">×</button>
            <div class="node-title-row">
                <span class="node-type-icon ${nodeType}">${getNodeIcon(node)}</span>
                <span class="node-title">${escapeHtml(getShortName(node.node_name))}</span>
                <span class="node-type-badge">${nodeType}</span>
            </div>
            <div class="node-fullname">${escapeHtml(node.node_name)}</div>
            <div class="detail-id-buttons">
                <button class="detail-id-btn">Trace: ${escapeHtml(traceId)}</button>
                ${sessionId ? `<button class="detail-id-btn">Session: ${escapeHtml(sessionId)}</button>` : ''}
            </div>
        </div>
        ${tagsHtml}
        <div class="node-metrics">
            <div class="metric">
                <div class="metric-label">Latency</div>
                <div class="metric-value">${duration}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Model</div>
                <div class="metric-value">${escapeHtml(model)}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Tokens</div>
                <div class="metric-value tokens">${tokensDisplay}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Cost</div>
                <div class="metric-value">${cost}</div>
            </div>
        </div>
        <div class="tabs">
            <button class="tab ${currentTab === 'preview' ? 'active' : ''}" onclick="switchTab('preview')">Preview</button>
            <button class="tab ${currentTab === 'log' ? 'active' : ''}" onclick="switchTab('log')">Log View</button>
        </div>
        <div class="tab-content">
    `;

    // Extract connects from metadata
    const inputConnects = (node.metadata && node.metadata.input_connects) || null;
    const outputConnects = (node.metadata && node.metadata.output_connects) || null;

    if (currentTab === 'preview') {
        // Preview tab - key-value tables
        if (node.input) {
            const inputHtml = renderKeyValueTable(node.input, { filterSystemVars: true, connects: inputConnects, connectDirection: 'input' });
            if (inputHtml && !inputHtml.includes('empty-table')) {
                html += `
                    <div class="node-section section-input">
                        <div class="section-header-styled input">
                            <span class="section-icon">↓</span>
                            <span class="section-label">INPUT</span>
                        </div>
                        ${inputHtml}
                    </div>
                `;
            }
        }

        if (node.output) {
            const outputHtml = renderKeyValueTable(node.output, { filterSystemVars: true, connects: outputConnects, connectDirection: 'output' });
            if (outputHtml && !outputHtml.includes('empty-table')) {
                html += `
                    <div class="node-section section-output">
                        <div class="section-header-styled output">
                            <span class="section-icon">↑</span>
                            <span class="section-label">OUTPUT</span>
                        </div>
                        ${outputHtml}
                    </div>
                `;
            }
        }

        if (node.metadata) {
            html += `
                <div class="node-section section-metadata">
                    <div class="section-header-styled metadata">
                        <span class="section-icon">≡</span>
                        <span class="section-label">METADATA</span>
                    </div>
                    ${renderKeyValueTable(node.metadata, { skipKeys: ['input_connects', 'output_connects'] })}
                </div>
            `;
        }
    } else {
        // Log view - raw JSON
        if (node.input) {
            html += `
                <div class="node-section section-input">
                    <div class="section-header-styled input">
                        <span class="section-icon">↓</span>
                        <span class="section-label">INPUT</span>
                    </div>
                    <div class="json-view">${formatJson(node.input)}</div>
                </div>
            `;
        }

        if (node.output) {
            html += `
                <div class="node-section section-output">
                    <div class="section-header-styled output">
                        <span class="section-icon">↑</span>
                        <span class="section-label">OUTPUT</span>
                    </div>
                    <div class="json-view">${formatJson(node.output)}</div>
                </div>
            `;
        }

        if (node.metadata) {
            html += `
                <div class="node-section section-metadata">
                    <div class="section-header-styled metadata">
                        <span class="section-icon">≡</span>
                        <span class="section-label">METADATA</span>
                    </div>
                    <div class="json-view">${formatJson(node.metadata)}</div>
                </div>
            `;
        }
    }

    html += '</div>';
    panel.innerHTML = html;
}

function switchTab(tab) {
    currentTab = tab;
    if (selectedNode) {
        renderNodeDetail(selectedNode);
    }
}

function renderKeyValueTable(obj, options = {}) {
    if (!obj) return '';

    const { filterSystemVars = false, connects = null, connectDirection = null, skipKeys = [] } = options;

    // Handle string values
    if (typeof obj === 'string') {
        return `<div class="kv-table"><div class="kv-row"><div class="kv-value full">${escapeHtml(obj)}</div></div></div>`;
    }

    // Handle arrays
    if (Array.isArray(obj)) {
        if (obj.length === 0) return '<div class="kv-table"><div class="kv-row"><div class="kv-value"><span class="kv-null">[]</span></div></div></div>';

        // Check if all elements are primitives — render as inline chips
        const allPrimitive = obj.every(v => v === null || typeof v !== 'object');
        if (allPrimitive) {
            let html = '<div class="array-inline">';
            for (let i = 0; i < obj.length; i++) {
                html += `<span class="array-chip"><span class="array-index">${i}</span>${formatValue(obj[i])}</span>`;
            }
            html += '</div>';
            return html;
        }

        // Complex arrays — render as indexed cards
        let html = '<div class="array-cards">';
        for (let i = 0; i < obj.length; i++) {
            const val = obj[i];
            html += `<div class="array-card">`;
            html += `<div class="array-card-header"><span class="array-card-index">${i}</span></div>`;
            if (typeof val === 'object' && val !== null) {
                html += `<div class="array-card-body">${renderKeyValueTable(val, options)}</div>`;
            } else {
                html += `<div class="array-card-body">${formatValue(val)}</div>`;
            }
            html += `</div>`;
        }
        html += '</div>';
        return html;
    }

    // Handle objects
    if (typeof obj === 'object' && obj !== null) {
        let keys = Object.keys(obj);

        // Filter out system variables (starting with @ or $)
        if (filterSystemVars) {
            keys = keys.filter(k => !k.startsWith('@') && !k.startsWith('$'));
        }

        // Skip specified keys
        if (skipKeys.length > 0) {
            keys = keys.filter(k => !skipKeys.includes(k));
        }

        if (keys.length === 0) return '<div class="kv-table empty-table"></div>';
        // Check if this object has code_fn (to skip function_name)
        const hasCodeFn = 'code_fn' in obj;

        let html = '<div class="kv-table">';
        for (const key of keys) {
            const val = obj[key];
            const keyHtml = connects ? renderLinkedKey(key, connects, connectDirection) : `<div class="kv-key">${escapeHtml(key)}</div>`;

            // Skip function_name if code_fn exists (it's redundant)
            if (key === 'function_name' && hasCodeFn) {
                continue;
            }

            // Special rendering for id - monospace hash style
            if (key === 'id' && typeof val === 'string') {
                html += `<div class="kv-row">${keyHtml}<div class="kv-value"><span class="meta-id">${escapeHtml(val)}</span></div></div>`;
                continue;
            }

            // Special rendering for type - colored badge with icon
            if (key === 'type' && typeof val === 'string') {
                const typeClass = val.toLowerCase().replace(/[^a-z0-9-]/g, '-');
                const icon = getNodeIcon({ node_type: val });
                html += `<div class="kv-row">${keyHtml}<div class="kv-value"><span class="meta-type-badge ${typeClass}"><span class="meta-type-icon ${typeClass}">${icon}</span>${escapeHtml(val)}</span></div></div>`;
                continue;
            }

            // Special rendering for name - show as path breadcrumb
            if (key === 'name' && typeof val === 'string' && val.includes('.')) {
                html += `<div class="kv-row">${keyHtml}<div class="kv-value"><div class="node-path">${formatNodePath(val)}</div></div></div>`;
                continue;
            }

            // Special rendering for code_fn - format as code block with syntax highlighting
            if (key === 'code_fn' && typeof val === 'string') {
                html += `<div class="kv-row">${keyHtml}<div class="kv-value"><pre class="code-block">${highlightPython(val)}</pre></div></div>`;
                continue;
            }

            // Special rendering for input_connects - show as "varName ← source"
            if (key === 'input_connects' && typeof val === 'object' && val !== null) {
                html += renderConnectsTable(key, val, 'input');
                continue;
            }

            // Special rendering for output_connects - show as "varName → target"
            if (key === 'output_connects' && typeof val === 'object' && val !== null) {
                html += renderConnectsTable(key, val, 'output');
                continue;
            }

            if (typeof val === 'object' && val !== null) {
                html += `<div class="kv-row">${keyHtml}<div class="kv-value nested">${renderKeyValueTable(val, options)}</div></div>`;
            } else {
                html += `<div class="kv-row">${keyHtml}<div class="kv-value">${formatValue(val)}</div></div>`;
            }
        }
        html += '</div>';
        return html;
    }

    return `<div class="kv-table"><div class="kv-row"><div class="kv-value">${escapeHtml(String(obj))}</div></div></div>`;
}

// Resolve a single connect value to { nodeName, fullNodeName, outputKey, isNodeRef }
function resolveConnectRef(source) {
    if (typeof source === 'object' && source !== null && !Array.isArray(source)) {
        const entries = Object.entries(source);
        if (entries.length === 1) {
            const [nodeKey, outputKey] = entries[0];
            if (typeof nodeKey === 'string' && typeof outputKey === 'string') {
                const nodeExists = findNodeByName(currentNodes, nodeKey);
                if (nodeExists) {
                    const lastDot = nodeKey.lastIndexOf('.');
                    return { nodeName: lastDot >= 0 ? nodeKey.slice(lastDot + 1) : nodeKey, fullNodeName: nodeKey, outputKey, isNodeRef: true };
                }
            }
        }
    } else if (typeof source === 'string') {
        const nodeExists = findNodeByName(currentNodes, source);
        if (nodeExists) {
            const lastDot = source.lastIndexOf('.');
            return { nodeName: lastDot >= 0 ? source.slice(lastDot + 1) : source, fullNodeName: source, outputKey: null, isNodeRef: true };
        }
    }
    return { isNodeRef: false };
}

// Render a kv-key that is linked to its connect source/target
function renderLinkedKey(key, connects, direction) {
    const source = connects ? connects[key] : null;
    if (!source) {
        return `<div class="kv-key">${escapeHtml(key)}</div>`;
    }

    const ref = resolveConnectRef(source);
    if (!ref.isNodeRef) {
        // Raw value connect — show as tooltip only
        const rawVal = JSON.stringify(source);
        return `<div class="kv-key has-connect" data-tooltip="${direction === 'input' ? '← ' : '→ '}${escapeHtml(rawVal)}">${escapeHtml(key)}</div>`;
    }

    const arrow = direction === 'input' ? '←' : '→';
    const tooltip = ref.outputKey
        ? `${arrow} ${ref.fullNodeName}["${ref.outputKey}"]`
        : `${arrow} ${ref.fullNodeName}`;

    const highlightKey = ref.outputKey ? escapeHtml(ref.outputKey) : escapeHtml(key);
    return `<div class="kv-key has-connect linked" data-tooltip="${escapeHtml(tooltip)}" onclick="event.stopPropagation(); selectNodeByName('${escapeHtml(ref.fullNodeName)}', '${highlightKey}')">${escapeHtml(key)}</div>`;
}

function renderConnectsTable(key, connects, direction) {
    const entries = Object.entries(connects).filter(([_, val]) => val !== null && val !== undefined);

    if (entries.length === 0) {
        return ''; // Hide if all values are null
    }

    let html = `<div class="kv-row"><div class="kv-key">${escapeHtml(key)}</div><div class="kv-value nested"><div class="connects-table">`;

    for (const [varName, source] of entries) {
        let refHtml = '';
        let isNodeRef = false;

        // Check if source is a node reference or a raw value
        if (typeof source === 'object' && source !== null && !Array.isArray(source)) {
            // Object format: {"full.node.name": "output_key"} - check if key is a valid node
            const sourceEntries = Object.entries(source);
            if (sourceEntries.length === 1) {
                const [nodeKey, outputKey] = sourceEntries[0];
                if (typeof nodeKey === 'string' && typeof outputKey === 'string') {
                    // Check if this node actually exists
                    const nodeExists = findNodeByName(currentNodes, nodeKey);
                    if (nodeExists) {
                        const fullNodeName = nodeKey;
                        const lastDot = fullNodeName.lastIndexOf('.');
                        const nodeName = lastDot >= 0 ? fullNodeName.slice(lastDot + 1) : fullNodeName;

                        refHtml = `<span class="connect-ref" title="${escapeHtml(fullNodeName)}" onclick="selectNodeByName('${escapeHtml(fullNodeName)}')">${escapeHtml(nodeName)}</span><span class="connect-key">["${escapeHtml(outputKey)}"]</span>`;
                        isNodeRef = true;
                    }
                }
            }
        } else if (typeof source === 'string') {
            // String format: "full.node.name" - check if node exists
            const nodeExists = findNodeByName(currentNodes, source);
            if (nodeExists) {
                const fullNodeName = source;
                const lastDot = source.lastIndexOf('.');
                const nodeName = lastDot >= 0 ? source.slice(lastDot + 1) : source;

                refHtml = `<span class="connect-ref" title="${escapeHtml(fullNodeName)}" onclick="selectNodeByName('${escapeHtml(fullNodeName)}')">${escapeHtml(nodeName)}</span>`;
                isNodeRef = true;
            }
        }

        // If not a node reference, display as raw value
        if (!isNodeRef) {
            const rawValue = JSON.stringify(source);
            refHtml = `<span class="connect-raw">${escapeHtml(rawValue)}</span>`;
        }

        if (direction === 'input') {
            html += `<div class="connect-row input">
                <span class="connect-var">${escapeHtml(varName)}</span>
                <span class="connect-arrow">${isNodeRef ? '←' : '='}</span>
                ${refHtml}
            </div>`;
        } else {
            html += `<div class="connect-row output">
                <span class="connect-var">${escapeHtml(varName)}</span>
                <span class="connect-arrow">→</span>
                ${refHtml}
            </div>`;
        }
    }

    html += '</div></div></div>';
    return html;
}

function selectNodeByName(nodeName, highlightKey) {
    const node = findNodeByName(currentNodes, nodeName);
    if (node) {
        selectNode(node, highlightKey);
    }
}

function findNodeByName(nodes, name) {
    for (const node of nodes) {
        if (node.node_name === name) return node;
        if (node.children) {
            const found = findNodeByName(node.children, name);
            if (found) return found;
        }
    }
    return null;
}

function backToList() {
    currentView = 'list';
    selectedNode = null;
    renderTraceList(currentTraces, null);
}

// Utility functions
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function formatTime(isoString) {
    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch {
        return isoString;
    }
}

// Format a primitive value with type-based syntax highlighting
function formatValue(val) {
    if (val === null) {
        return '<span class="kv-null">null</span>';
    }
    if (val === undefined) {
        return '<span class="kv-null">undefined</span>';
    }
    if (typeof val === 'boolean') {
        const cls = val ? 'kv-boolean-true' : 'kv-boolean-false';
        return `<span class="${cls}">${val}</span>`;
    }
    if (typeof val === 'number') {
        return `<span class="kv-number">${val}</span>`;
    }
    // String value
    return `<span class="kv-string">${escapeHtml(String(val))}</span>`;
}

// Format node name as breadcrumb chain
function formatNodePath(name) {
    if (!name) return '';
    const parts = name.split('.');
    return parts.map((part, i) => {
        const isLast = i === parts.length - 1;
        return `<span class="path-segment${isLast ? ' current' : ''}">${escapeHtml(part)}</span>`;
    }).join('<span class="path-separator">›</span>');
}

function formatDuration(ms) {
    if (ms < 1000) {
        return ms.toFixed(0) + 'ms';
    }
    return (ms / 1000).toFixed(2) + 's';
}

function formatJson(obj) {
    if (!obj) return '';
    try {
        const json = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
        return syntaxHighlight(json);
    } catch {
        return escapeHtml(String(obj));
    }
}

function syntaxHighlight(json) {
    // Escape HTML first
    json = escapeHtml(json);

    // Apply syntax highlighting
    return json.replace(
        /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
        function (match) {
            let cls = 'json-number';
            if (/^"/.test(match)) {
                if (/:$/.test(match)) {
                    cls = 'json-key';
                    match = match.slice(0, -1) + '</span>:';
                    return '<span class="' + cls + '">' + match;
                } else {
                    cls = 'json-string';
                }
            } else if (/true|false/.test(match)) {
                cls = 'json-boolean';
            } else if (/null/.test(match)) {
                cls = 'json-null';
            }
            return '<span class="' + cls + '">' + match + '</span>';
        }
    );
}

function highlightPython(code) {
    if (!code) return '';

    const keywords = new Set(['def', 'class', 'if', 'elif', 'else', 'for', 'while', 'try', 'except', 'finally',
                      'with', 'as', 'import', 'from', 'return', 'yield', 'raise', 'pass', 'break',
                      'continue', 'and', 'or', 'not', 'in', 'is', 'lambda', 'None', 'True', 'False',
                      'async', 'await', 'global', 'nonlocal', 'assert', 'del']);

    const builtins = new Set(['print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict', 'set', 'tuple',
                      'open', 'input', 'type', 'isinstance', 'hasattr', 'getattr', 'setattr', 'super',
                      'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed', 'sum', 'min', 'max',
                      'abs', 'round', 'any', 'all', 'format', 'repr', 'sleep']);

    // Tokenize and highlight
    const lines = code.split('\n');
    const result = [];

    for (const line of lines) {
        let html = '';
        let i = 0;

        while (i < line.length) {
            // Check for comment
            if (line[i] === '#') {
                html += `<span class="py-comment">${escapeHtml(line.slice(i))}</span>`;
                break;
            }

            // Check for decorator
            if (line[i] === '@' && (i === 0 || /\s/.test(line[i-1]))) {
                const match = line.slice(i).match(/^@\w+/);
                if (match) {
                    html += `<span class="py-decorator">${escapeHtml(match[0])}</span>`;
                    i += match[0].length;
                    continue;
                }
            }

            // Check for strings
            if (line[i] === '"' || line[i] === "'") {
                const quote = line[i];
                const triple = line.slice(i, i + 3) === quote.repeat(3);
                const endQuote = triple ? quote.repeat(3) : quote;
                const start = i;
                i += triple ? 3 : 1;

                while (i < line.length) {
                    if (line[i] === '\\') {
                        i += 2;
                    } else if (line.slice(i, i + endQuote.length) === endQuote) {
                        i += endQuote.length;
                        break;
                    } else {
                        i++;
                    }
                }
                html += `<span class="py-string">${escapeHtml(line.slice(start, i))}</span>`;
                continue;
            }

            // Check for numbers
            if (/\d/.test(line[i]) && (i === 0 || !/\w/.test(line[i-1]))) {
                const match = line.slice(i).match(/^\d+\.?\d*/);
                if (match) {
                    html += `<span class="py-number">${match[0]}</span>`;
                    i += match[0].length;
                    continue;
                }
            }

            // Check for identifiers/keywords
            if (/[a-zA-Z_]/.test(line[i])) {
                const match = line.slice(i).match(/^[a-zA-Z_]\w*/);
                if (match) {
                    const word = match[0];
                    const nextChar = line[i + word.length] || '';

                    if (keywords.has(word)) {
                        html += `<span class="py-keyword">${word}</span>`;
                    } else if (builtins.has(word) && nextChar === '(') {
                        html += `<span class="py-builtin">${word}</span>`;
                    } else if (i > 0 && line.slice(Math.max(0, i - 4), i).match(/def\s+$/)) {
                        html += `<span class="py-function">${word}</span>`;
                    } else if (i > 0 && line.slice(Math.max(0, i - 6), i).match(/class\s+$/)) {
                        html += `<span class="py-class">${word}</span>`;
                    } else {
                        html += escapeHtml(word);
                    }
                    i += word.length;
                    continue;
                }
            }

            // Regular character
            html += escapeHtml(line[i]);
            i++;
        }

        result.push(html);
    }

    return result.join('\n');
}
