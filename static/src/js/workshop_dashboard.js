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

const MODE_CARDS = [
    {
        mode: "slab_finish",
        title: "Acabado de placas",
        subtitle: "Muchas placas entran; cada placa genera su salida individual.",
        icon: "✦",
    },
    {
        mode: "slab_cut",
        title: "Corte de placas",
        subtitle: "Varias placas pueden cubrir una demanda objetivo en m², con retazos y merma.",
        icon: "◫",
    },
    {
        mode: "format_process",
        title: "Formatos / pallets",
        subtitle: "Pallet/formato objetivo por m² con balance automático de área.",
        icon: "▦",
    },
    {
        mode: "rework",
        title: "Reproceso / reparación",
        subtitle: "Recuperación, reclasificación o reparación de material.",
        icon: "↻",
    },
];

class StoneWorkshopDashboard extends Component {
    static template = "stone_workshop.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            modeCards: MODE_CARDS,
            stats: {
                draft: 0,
                active: 0,
                done: 0,
                slab_finish: 0,
                slab_cut: 0,
                format_process: 0,
                rework: 0,
            },
            recentOrders: [],
        });

        onWillStart(async () => {
            await this.loadDashboard();
        });
    }

    async loadDashboard() {
        await Promise.all([this.loadStats(), this.loadOrders()]);
    }

    async loadStats() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [["state", "!=", "cancel"]],
            ["state", "operation_mode"]
        );
        this.state.stats = {
            draft: orders.filter((o) => o.state === "draft").length,
            active: orders.filter((o) => o.state === "in_workshop").length,
            done: orders.filter((o) => o.state === "done").length,
            slab_finish: orders.filter((o) => o.operation_mode === "slab_finish").length,
            slab_cut: orders.filter((o) => o.operation_mode === "slab_cut").length,
            format_process: orders.filter((o) => o.operation_mode === "format_process").length,
            rework: orders.filter((o) => o.operation_mode === "rework").length,
        };
    }

    async loadOrders() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [],
            [
                "name",
                "operation_mode",
                "process_id",
                "input_count",
                "output_count",
                "production_target_sqm",
                "area_in_total",
                "area_out_total",
                "area_remnant_total",
                "area_loss_total",
                "yield_percent",
                "area_balance_delta",
                "state",
            ],
            { order: "create_date desc", limit: 12 }
        );
        this.state.recentOrders = orders.map((order) => ({
            ...order,
            state_label: STATE_LABELS[order.state] || order.state,
        }));
    }

    openNew(mode) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nueva orden de taller",
            res_model: "workshop.order",
            views: [[false, "form"]],
            target: "current",
            context: {
                default_operation_mode: mode,
            },
        });
    }

    openOrders(domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Órdenes de Taller",
            res_model: "workshop.order",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
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
