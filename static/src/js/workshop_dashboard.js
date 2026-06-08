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
        subtitle: "Demanda en m² con subproductos",
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
                done_today: 0,
                area_cut_today: 0,
                area_finish_today: 0,
                area_format_today: 0,
                avg_yield_today: 0,
                loss_today: 0,
                loss_percent_today: 0,
                area_done_today: 0,
                wip_orders: 0,
                wip_area: 0,
                wip_slabs: 0,
                parked_orders: 0,
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
                this.loadBoard(),
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

    // Decora una orden del panel (payload del servidor) con etiquetas y el
    // timestamp de la sesión activa convertido a ms para el cronómetro en vivo.
    _decorateBoard(order, isNext = false) {
        return {
            ...order,
            state_label: STATE_LABELS[order.state] || order.state,
            mode_label: MODE_LABELS[order.operation_mode] || order.operation_mode,
            is_next: isNext,
            active_start_ms: parseOdooUtc(order.active_session_start),
        };
    }

    async loadKpis() {
        try {
            const kpis = await this.orm.call("workshop.order", "get_workshop_kpis", []);
            this.state.kpis = { ...this.state.kpis, ...kpis };
            if (kpis && kpis.mode_stats) {
                this.state.modeStats = kpis.mode_stats;
            }
        } catch (error) {
            console.error("[STONE WORKSHOP] loadKpis failed:", error);
        }
    }

    // Carga cola priorizada (borradores + órdenes devueltas a la cola por la
    // regla de 24 h) y las órdenes en ejecución, en una sola llamada.
    async loadBoard() {
        try {
            const board = await this.orm.call("workshop.order", "get_workshop_board", []);
            this.state.priorityQueue = (board.queue || []).map((o, idx) =>
                this._decorateBoard(o, idx === 0),
            );
            this.state.executingOrders = (board.execution || []).map((o) =>
                this._decorateBoard(o, false),
            );
        } catch (error) {
            console.error("[STONE WORKSHOP] loadBoard failed:", error);
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
            this.state.recentDone = orders.map((o) => ({
                ...o,
                state_label: STATE_LABELS[o.state] || o.state,
                mode_label: MODE_LABELS[o.operation_mode] || o.operation_mode,
            }));
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
        await this.loadBoard();
    }

    async resumeOrder(order) {
        try {
            await this.orm.call("workshop.order", "action_resume_timer", [[order.id]]);
        } catch (error) {
            console.error("[STONE WORKSHOP] resume failed:", error);
            this.notification.add("No se pudo reanudar la orden.", { type: "danger" });
        }
        await this.loadBoard();
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
            await this.loadBoard();
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
