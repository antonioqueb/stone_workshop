/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    draft: "Borrador",
    in_workshop: "En taller",
    done: "Terminada",
    cancel: "Cancelada",
};

const PRIORITY_LABELS = {
    "0": "Normal",
    "1": "Alta",
    "2": "Urgente",
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
        });

        onWillStart(async () => {
            await this.loadDashboard();
        });
    }

    async loadDashboard() {
        this.state.loading = true;
        try {
            await Promise.all([
                this.loadKpis(),
                this.loadPriorityQueue(),
                this.loadExecuting(),
                this.loadRecentDone(),
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
            priority_label: PRIORITY_LABELS[order.priority] || PRIORITY_LABELS["0"],
            mode_label: MODE_LABELS[order.operation_mode] || order.operation_mode,
        };
    }

    async loadKpis() {
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
    }

    async loadPriorityQueue() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "=", "draft"]],
            [
                "name",
                "priority",
                "process_id",
                "operation_mode",
                "responsible_id",
                "date_planned",
                "production_target_sqm",
                "area_in_total",
                "input_count",
                "state",
            ],
            { order: "priority desc, date_planned asc, id asc", limit: 15 },
        );
        this.state.priorityQueue = orders.map((o, idx) => ({
            ...this._decorate(o),
            is_next: idx === 0,
        }));
    }

    async loadExecuting() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "=", "in_workshop"]],
            [
                "name",
                "priority",
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
            ],
            { order: "priority desc, date_start asc, id asc", limit: 15 },
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
            };
        });
    }

    async loadRecentDone() {
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
    }

    async setPriority(orderId, newPriority) {
        await this.orm.write("workshop.order", [orderId], { priority: String(newPriority) });
        await Promise.all([this.loadPriorityQueue(), this.loadExecuting()]);
    }

    bumpPriority(order, direction) {
        const current = parseInt(order.priority || "0", 10);
        const next = Math.max(0, Math.min(2, current + direction));
        if (next !== current) {
            this.setPriority(order.id, next);
        }
    }

    fmt(value, decimals = 2) {
        return fmt(value, decimals);
    }

    priorityStars(priority) {
        if (priority === "2") return "★★★";
        if (priority === "1") return "★★";
        return "★";
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
