/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    draft: 'Borrador',
    confirmed: 'Confirmada',
    in_progress: 'En Proceso',
    done: 'Terminada',
    cancel: 'Cancelada',
};

class WorkshopDashboard extends Component {
    static template = "stone_workshop.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            processes: [],
            products: [],
            lots: [],
            selectedProcess: null,
            selectedLot: null,
            form: this._emptyForm(),
            cart: this._loadCart(),
            recentOrders: [],
            stats: { in_progress: 0, done: 0, finish: 0, cut: 0 },
        });

        onWillStart(async () => {
            await this.loadProcesses();
            await this.loadProducts();
            await this.loadOrders();
            await this.loadStats();
        });
    }

    get cartTotal() {
        return this.state.cart.reduce((sum, item) => sum + (item.total_cost || 0), 0);
    }

    _emptyForm() {
        return {
            product_in_id: false,
            product_in_name: '',
            lot_in_id: false,
            lot_in_name: '',
            qty_in: 0,
            product_out_id: false,
            product_out_name: '',
            qty_out: 0,
            lot_out_name: '',
            format_width: '',
            format_height: '',
            format_qty: 1,
            labor_cost: 0,
            area_sqm: 0,
            process_cost: 0,
            total_cost: 0,
        };
    }

    _loadCart() {
        try {
            const saved = localStorage.getItem('ws_cart');
            return saved ? JSON.parse(saved) : [];
        } catch {
            return [];
        }
    }

    _saveCart() {
        try {
            localStorage.setItem('ws_cart', JSON.stringify(this.state.cart));
        } catch { /* ignore */ }
    }

    async loadProcesses() {
        this.state.processes = await this.orm.searchRead(
            "workshop.process",
            [["active", "=", true]],
            ["name", "code", "process_type", "cost_per_sqm", "labor_cost"],
            { order: "sequence, name" }
        );
    }

    async loadProducts() {
        this.state.products = await this.orm.searchRead(
            "product.product",
            [["tracking", "=", "lot"], ["type", "=", "product"]],
            ["name", "display_name"],
            { order: "name", limit: 200 }
        );
    }

    async loadOrders() {
        const orders = await this.orm.searchRead(
            "workshop.order",
            [],
            ["name", "process_id", "product_in_id", "lot_in_id", "product_out_id",
             "lot_out_name", "total_cost", "state"],
            { order: "create_date desc", limit: 20 }
        );
        this.state.recentOrders = orders.map(o => ({
            ...o,
            state_label: STATE_LABELS[o.state] || o.state,
        }));
    }

    async loadStats() {
        const all = await this.orm.searchRead(
            "workshop.order",
            [["state", "not in", ["cancel"]]],
            ["state", "process_type"],
        );
        this.state.stats = {
            in_progress: all.filter(o => o.state === 'in_progress').length,
            done: all.filter(o => o.state === 'done').length,
            finish: all.filter(o => o.process_type === 'finish').length,
            cut: all.filter(o => o.process_type === 'cut').length,
        };
    }

    selectProcess(proc) {
        this.state.selectedProcess = proc;
        this.state.selectedLot = null;
        this.state.lots = [];
        Object.assign(this.state.form, this._emptyForm());
    }

    async onProductInChange(ev) {
        const productId = parseInt(ev.target.value) || false;
        this.state.form.product_in_id = productId;
        const prod = this.state.products.find(p => p.id === productId);
        this.state.form.product_in_name = prod ? prod.display_name : '';
        this.state.selectedLot = null;

        if (productId) {
            const quants = await this.orm.searchRead(
                "stock.quant",
                [["product_id", "=", productId], ["location_id.usage", "=", "internal"], ["quantity", ">", 0]],
                ["lot_id", "quantity"],
            );
            const lotMap = {};
            for (const q of quants) {
                if (q.lot_id) {
                    const lid = q.lot_id[0];
                    if (!lotMap[lid]) {
                        lotMap[lid] = { id: lid, name: q.lot_id[1], qty: 0 };
                    }
                    lotMap[lid].qty += q.quantity;
                }
            }
            this.state.lots = Object.values(lotMap);
        } else {
            this.state.lots = [];
        }
    }

    onLotChange(ev) {
        const lotId = parseInt(ev.target.value) || false;
        const lot = this.state.lots.find(l => l.id === lotId);
        this.state.selectedLot = lot || null;
        if (lot) {
            this.state.form.lot_in_id = lot.id;
            this.state.form.lot_in_name = lot.name;
            this.state.form.qty_in = lot.qty;
            // Auto-set qty_out = qty_in
            this.state.form.qty_out = lot.qty;
            this._updateLotOutName();
            this._recalcCosts();
        }
    }

    onProductOutChange(ev) {
        const productId = parseInt(ev.target.value) || false;
        this.state.form.product_out_id = productId;
        const prod = this.state.products.find(p => p.id === productId);
        this.state.form.product_out_name = prod ? prod.display_name : '';
        this._recalcCosts();
    }

    updateFormNum(field, value) {
        const num = parseFloat(value) || 0;
        this.state.form[field] = num;
        this._recalcCosts();
    }

    updateFormText(field, value) {
        this.state.form[field] = value;
        this._recalcCosts();
    }

    _updateLotOutName() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (f.lot_in_name && proc) {
            f.lot_out_name = `${f.lot_in_name}-${proc.code}`;
        }
    }

    _parseDimension(val) {
        if (!val) return 0;
        const num = parseFloat(val);
        return isNaN(num) ? 0 : num;
    }

    _recalcCosts() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (!proc) return;

        if (proc.process_type === 'cut') {
            const w = this._parseDimension(f.format_width);
            const h = this._parseDimension(f.format_height);
            const qty = parseInt(f.format_qty) || 0;
            if (w && h && qty) {
                f.area_sqm = (w / 100) * (h / 100) * qty;
            } else {
                f.area_sqm = 0;
            }
        } else {
            f.area_sqm = f.qty_in || 0;
        }

        f.process_cost = f.area_sqm * (proc.cost_per_sqm || 0);
        f.total_cost = f.process_cost + (parseFloat(f.labor_cost) || 0);
    }

    addToCart() {
        const f = this.state.form;
        const proc = this.state.selectedProcess;
        if (!f.product_in_id || !f.lot_in_id || !f.product_out_id || !proc) {
            this.notification.add("Completa todos los campos antes de agregar.", { type: "warning" });
            return;
        }

        const item = {
            key: Date.now() + '_' + Math.random().toString(36).substr(2, 5),
            process_id: proc.id,
            process_name: proc.name,
            process_type: proc.process_type,
            process_code: proc.code,
            product_in_id: f.product_in_id,
            product_in_name: f.product_in_name,
            lot_in_id: f.lot_in_id,
            lot_in_name: f.lot_in_name,
            qty_in: f.qty_in,
            qty_out: f.qty_out,
            product_out_id: f.product_out_id,
            product_out_name: f.product_out_name,
            lot_out_name: f.lot_out_name,
            format_width: f.format_width,
            format_height: f.format_height,
            format_qty: f.format_qty,
            labor_cost: parseFloat(f.labor_cost) || 0,
            area_sqm: f.area_sqm,
            process_cost: f.process_cost,
            total_cost: f.total_cost,
        };

        this.state.cart.push(item);
        this._saveCart();
        this.notification.add(`${proc.name} agregado al carrito`, { type: "success" });

        Object.assign(this.state.form, this._emptyForm());
        this.state.selectedLot = null;
        this.state.lots = [];
    }

    removeFromCart(key) {
        this.state.cart = this.state.cart.filter(i => i.key !== key);
        this._saveCart();
    }

    clearCart() {
        this.state.cart = [];
        this._saveCart();
    }

    async processCart() {
        if (!this.state.cart.length) return;

        try {
            for (const item of this.state.cart) {
                await this.orm.create("workshop.order", [{
                    process_id: item.process_id,
                    product_in_id: item.product_in_id,
                    lot_in_id: item.lot_in_id,
                    qty_in: item.qty_in,
                    product_out_id: item.product_out_id,
                    qty_out: item.qty_out,
                    format_width: item.format_width || '',
                    format_height: item.format_height || '',
                    format_qty: item.format_qty || 0,
                    labor_cost: item.labor_cost || 0,
                }]);
            }
            this.notification.add(
                `${this.state.cart.length} orden(es) creada(s) exitosamente`,
                { type: "success" }
            );
            this.clearCart();
            await this.loadOrders();
            await this.loadStats();
        } catch (e) {
            this.notification.add(`Error al crear órdenes: ${e.message}`, { type: "danger" });
        }
    }

    openOrder(orderId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            res_id: orderId,
            views: [[false, "form"]],
        });
    }

    openOdooForm() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "workshop.order",
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("workshop_dashboard", WorkshopDashboard);