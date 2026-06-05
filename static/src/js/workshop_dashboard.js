/** @odoo-module **/

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Convierte un datetime de Odoo ("YYYY-MM-DD HH:MM:SS", UTC naive) a ms epoch.
function parseOdooUtc(value) {
    if (!value) return null;
    const ms = Date.parse(String(value).replace(" ", "T") + "Z");
    return Number.isNaN(ms) ? null : ms;
}

function formatDuration(totalSeconds) {
    const s = Math.max(0, Math.round(totalSeconds || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(h)}:${pad(m)}:${pad(sec)}`;
}

const STATE_LABELS = {
    draft: "Borrador",
    in_workshop: "En taller",
    done: "Terminada",
    cancel: "Cancelada",
};

const MODE_LABELS = {
    slab_finish: "Acabado de placas",
    slab_cut: "Corte de placas",
    format_process: "Formatos / pallets",
    rework: "Reproceso",
};

const MODE_CARDS = [
    {
        mode: "slab_finish",
        title: "Acabado",
        subtitle: "Placa a placa",
        icon: "✦",
    },
    {
        mode: "slab_cut",
        title: "Corte",
        subtitle: "Demanda en m² con retazos",
        icon: "◫",
    },
    {
        mode: "format_process",
        title: "Formatos",
        subtitle: "Pallet o piezas por m²",
        icon: "▦",
    },
    {
        mode: "rework",
        title: "Reproceso",
        subtitle: "Reparación o reclasificación",
        icon: "↻",
    },
];

function fmt(value, decimals = 2) {
    if (value === null || value === undefined) return "0";
    const num = typeof value === "number" ? value : parseFloat(value);
    if (Number.isNaN(num)) return "0";
    return num.toFixed(decimals);
}

class StoneWorkshopDashboard extends Component {
    static template = "stone_workshop.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            modeCards: MODE_CARDS,
            modeLabels: MODE_LABELS,
            kpis: {
                draft: 0,
                in_workshop: 0,
                done_today: 0,
                area_today: 0,
            },
            modeStats: {
                slab_finish: 0,
                slab_cut: 0,
                format_process: 0,
                rework: 0,
            },
            priorityQueue: [],
            executingOrders: [],
            recentDone: [],
            loading: true,
            lastRefresh: null,
            draggingId: null,
            dragOverId: null,
            tick: 0,
            capacity: { next_slot_days: 0, capacity_hours: 8, backlog_hours: 0, next_slot_date: "" },
            access: { can_reorder: true, can_set_priority: true },
        });

        this._tickInterval = null;

        // No bloqueamos el primer render con las RPC: el panel se pinta de
        // inmediato (shell) y los datos llegan después. Así "entrar" es instantáneo
        // aunque alguna consulta tarde, y si una falla no tumba todo el panel.
        onMounted(() => {
            this.loadDashboard();
            // Tick cada segundo para refrescar los cronómetros en vivo.
            this._tickInterval = setInterval(() => {
                this.state.tick = (this.state.tick + 1) % 1000000;
            }, 1000);
        });

        onWillUnmount(() => {
            if (this._tickInterval) {
                clearInterval(this._tickInterval);
                this._tickInterval = null;
            }
        });
    }

    async loadDashboard() {
        this.state.loading = true;
        // allSettled: cada bloque carga independiente; si uno falla, los demás
        // siguen mostrándose (cada loader ya captura su propio error).
        try {
            await Promise.allSettled([
                this.loadKpis(),
                this.loadPriorityQueue(),
                this.loadExecuting(),
                this.loadRecentDone(),
                this.loadCapacity(),
                this.loadAccess(),
            ]);
        } finally {
            this.state.loading = false;
            const d = new Date();
            this.state.lastRefresh =
                d.getHours().toString().padStart(2, "0") +
                ":" +
                d.getMinutes().toString().padStart(2, "0");
        }
    }

    _decorate(order) {
        return {
            ...order,
            state_label: STATE_LABELS[order.state] || order.state,
            mode_label: MODE_LABELS[order.operation_mode] || order.operation_mode,
        };
    }

    async loadKpis() {
        try {
            const today = new Date();
            const todayStart =
                today.getFullYear() +
                "-" +
                String(today.getMonth() + 1).padStart(2, "0") +
                "-" +
                String(today.getDate()).padStart(2, "0") +
                " 00:00:00";

            const [active, doneToday] = await Promise.all([
                this.orm.searchRead(
                    "workshop.order",
                    [["state", "in", ["draft", "in_workshop"]]],
                    ["state", "operation_mode"],
                ),
                this.orm.searchRead(
                    "workshop.order",
                    [
                        ["state", "=", "done"],
                        ["date_done", ">=", todayStart],
                    ],
                    ["area_out_total"],
                ),
            ]);

            this.state.kpis = {
                draft: active.filter((o) => o.state === "draft").length,
                in_workshop: active.filter((o) => o.state === "in_workshop").length,
                done_today: doneToday.length,
                area_today: doneToday.reduce((s, o) => s + (o.area_out_total || 0), 0),
            };
            this.state.modeStats = {
                slab_finish: active.filter((o) => o.operation_mode === "slab_finish").length,
                slab_cut: active.filter((o) => o.operation_mode === "slab_cut").length,
                format_process: active.filter((o) => o.operation_mode === "format_process").length,
                rework: active.filter((o) => o.operation_mode === "rework").length,
            };
        } catch (error) {
            console.error("[STONE WORKSHOP] loadKpis failed:", error);
        }
    }

    async loadCapacity() {
        try {
            const ov = await this.orm.call("workshop.order", "get_workshop_capacity_overview", []);
            this.state.capacity = ov || this.state.capacity;
        } catch (error) {
            console.error("[STONE WORKSHOP] capacity overview failed:", error);
        }
    }

    async loadAccess() {
        try {
            const acc = await this.orm.call("workshop.order", "get_workshop_dashboard_access", []);
            this.state.access = acc || this.state.access;
        } catch (error) {
            console.error("[STONE WORKSHOP] access check failed:", error);
        }
    }

    async loadPriorityQueue() {
        try {
            const orders = await this.orm.searchRead(
                "workshop.order",
                [["state", "=", "draft"]],
                [
                    "name",
                    "queue_sequence",
                    "process_id",
                    "operation_mode",
                    "responsible_id",
                    "date_planned",
                    "production_target_sqm",
                    "area_in_total",
                    "input_count",
                    "state",
                    "estimated_days",
                    "has_estimate",
                ],
                { order: "queue_sequence asc, create_date asc, id asc", limit: 15 },
            );
            this.state.priorityQueue = orders.map((o, idx) => ({
                ...this._decorate(o),
                is_next: idx === 0,
                estimated_days: o.estimated_days || 0,
                has_estimate: !!o.has_estimate,
            }));
        } catch (error) {
            console.error("[STONE WORKSHOP] loadPriorityQueue failed:", error);
        }
    }

    async loadExecuting() {
        try {
            const orders = await this.orm.searchRead(
                "workshop.order",
                [["state", "=", "in_workshop"]],
                [
                    "name",
                    "process_id",
                    "operation_mode",
                    "responsible_id",
                    "date_start",
                    "production_target_sqm",
                    "area_in_total",
                    "area_out_total",
                    "progress_log_count",
                    "input_count",
                    "state",
                    "timer_running",
                    "active_session_start",
                    "worked_seconds_closed",
                    "estimated_minutes",
                    "estimated_days",
                    "has_estimate",
                ],
                { order: "date_start asc, id asc", limit: 15 },
            );
            this.state.executingOrders = orders.map((o) => {
                const target = o.production_target_sqm || o.area_in_total || 0;
                const done = o.area_out_total || 0;
                const progress = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
                return {
                    ...this._decorate(o),
                    progress,
                    target_area: target,
                    done_area: done,
                    timer_running: !!o.timer_running,
                    worked_seconds_closed: o.worked_seconds_closed || 0,
                    active_start_ms: parseOdooUtc(o.active_session_start),
                    estimated_minutes: o.estimated_minutes || 0,
                    estimated_days: o.estimated_days || 0,
                    has_estimate: !!o.has_estimate,
                };
            });
        } catch (error) {
            console.error("[STONE WORKSHOP] loadExecuting failed:", error);
        }
    }

    // Tiempo trabajado en vivo (s). Depende de state.tick para refrescar cada segundo.
    liveSeconds(order) {
        void this.state.tick;
        let total = order.worked_seconds_closed || 0;
        if (order.timer_running && order.active_start_ms) {
            total += Math.max(0, (Date.now() - order.active_start_ms) / 1000);
        }
        return total;
    }

    liveTime(order) {
        return formatDuration(this.liveSeconds(order));
    }

    // % de avance por tiempo (vivo). Decrementa el restante conforme se consume.
    timeProgress(order) {
        const est = order.estimated_minutes || 0;
        if (est <= 0) return 0;
        const workedMin = this.liveSeconds(order) / 60;
        return Math.min(100, Math.round((workedMin / est) * 100));
    }

    // Días restantes (vivo) = (estimado − trabajado) / (60 × 8).
    remainingDays(order) {
        const est = order.estimated_minutes || 0;
        if (est <= 0) return 0;
        const workedMin = this.liveSeconds(order) / 60;
        const remaining = Math.max(0, est - workedMin);
        return remaining / 60 / 8;
    }

    fmtDays(value) {
        const n = parseFloat(value || 0);
        if (!Number.isFinite(n) || n <= 0) return "0";
        return n < 10 ? n.toFixed(1) : Math.round(n).toString();
    }

    async loadRecentDone() {
        try {
            const orders = await this.orm.searchRead(
                "workshop.order",
                [["state", "=", "done"]],
                [
                    "name",
                    "process_id",
                    "operation_mode",
                    "responsible_id",
                    "date_done",
                    "area_in_total",
                    "area_out_total",
                    "yield_percent",
                ],
                { order: "date_done desc, id desc", limit: 6 },
            );
            this.state.recentDone = orders.map((o) => this._decorate(o));
        } catch (error) {
            console.error("[STONE WORKSHOP] loadRecentDone failed:", error);
        }
    }

    fmt(value, decimals = 2) {
        return fmt(value, decimals);
    }

    // ─── Pausar / reanudar cronómetro ────────────────────────────────────
    // Pausa directa de un clic, sin pedir motivo. El motivo se captura a mano
    // (opcional) en la pestaña "Tiempos" de la orden cuando se quiera.
    async pauseOrder(order) {
        try {
            await this.orm.call("workshop.order", "action_pause_timer", [[order.id]]);
        } catch (error) {
            console.error("[STONE WORKSHOP] pause failed:", error);
            this.notification.add("No se pudo pausar la orden.", { type: "danger" });
        }
        await this.loadExecuting();
    }

    async resumeOrder(order) {
        try {
            await this.orm.call("workshop.order", "action_resume_timer", [[order.id]]);
        } catch (error) {
            console.error("[STONE WORKSHOP] resume failed:", error);
            this.notification.add("No se pudo reanudar la orden.", { type: "danger" });
        }
        await this.loadExecuting();
    }

    // ─── Drag-and-drop de la cola ────────────────────────────────────────
    onQueueDragStart(event, order) {
        if (!this.state.access.can_reorder) {
            event.preventDefault();
            return;
        }
        this.state.draggingId = order.id;
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            // Algunos navegadores requieren un setData no vacío para iniciar el drag.
            event.dataTransfer.setData("text/plain", String(order.id));
        }
    }

    onQueueDragOver(event, order) {
        if (this.state.draggingId === null || this.state.draggingId === order.id) {
            return;
        }
        event.preventDefault();
        if (event.dataTransfer) {
            event.dataTransfer.dropEffect = "move";
        }
        if (this.state.dragOverId !== order.id) {
            this.state.dragOverId = order.id;
        }
    }

    onQueueDragLeave(event, order) {
        if (this.state.dragOverId === order.id) {
            this.state.dragOverId = null;
        }
    }

    async onQueueDrop(event, targetOrder) {
        event.preventDefault();
        const draggedId = this.state.draggingId;
        this.state.draggingId = null;
        this.state.dragOverId = null;
        if (draggedId === null || draggedId === targetOrder.id) {
            return;
        }

        const queue = [...this.state.priorityQueue];
        const fromIndex = queue.findIndex((o) => o.id === draggedId);
        const toIndex = queue.findIndex((o) => o.id === targetOrder.id);
        if (fromIndex < 0 || toIndex < 0) {
            return;
        }

        const [moved] = queue.splice(fromIndex, 1);
        queue.splice(toIndex, 0, moved);
        // Re-decorar para refrescar `is_next` (la primera fila pasa a ser la siguiente).
        this.state.priorityQueue = queue.map((o, idx) => ({ ...o, is_next: idx === 0 }));

        try {
            await this.orm.call(
                "workshop.order",
                "reorder_workshop_queue",
                [queue.map((o) => o.id)],
            );
        } catch (error) {
            console.error("[STONE WORKSHOP] reorder failed:", error);
            this.notification.add("No se pudo guardar el nuevo orden de la cola.", { type: "danger" });
            await this.loadPriorityQueue();
        }
    }

    onQueueDragEnd() {
        this.state.draggingId = null;
        this.state.dragOverId = null;
    }

    openNew(mode) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nueva orden de taller",
            res_model: "workshop.order",
            views: [[false, "form"]],
            target: "current",
            context: { default_operation_mode: mode },
        });
    }

    openOrders(domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Órdenes de Taller",
            res_model: "workshop.order",
            views: [
                [false, "kanban"],
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
            domain,
        });
    }

    openOrder(orderId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            res_id: orderId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("stone_workshop_dashboard", StoneWorkshopDashboard);
