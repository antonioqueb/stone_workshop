/** @odoo-module **/

import { Component, useState, onWillStart, onWillUpdateProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

export class WorkshopTicketSelector extends Component {
    static template = "stone_workshop.WorkshopTicketSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            groups: [],
            collapsed: {},
            isLoading: true,
        });
        this._writeTimeout = null;

        onWillStart(async () => {
            await this.loadGroups();
            this.writeSelectionsToRecord();
        });

        onWillUpdateProps(async () => {
            // No recargar automáticamente para no perder la selección local.
        });
    }

    extractId(value) {
        if (!value) return false;
        if (typeof value === "number") return value;
        if (Array.isArray(value)) return value[0] || false;
        if (typeof value === "object") return value.resId || value.id || value[0] || false;
        return false;
    }

    getRootRecord() {
        return this.props.record?.model?.root || this.props.record;
    }

    getOrderId() {
        const root = this.getRootRecord();
        const data = root?.data || {};
        return this.extractId(data.order_id) || this.extractId(data.active_id);
    }

    getEditingTicketId() {
        const root = this.getRootRecord();
        const data = root?.data || {};
        return this.extractId(data.editing_ticket_id);
    }

    async loadGroups() {
        this.state.isLoading = true;
        try {
            const orderId = this.getOrderId();
            if (!orderId) {
                this.state.groups = [];
                return;
            }

            const groups = await this.orm.call(
                "workshop.order",
                "get_workshop_ticket_selector_data",
                [[orderId]],
                { editing_ticket_id: this.getEditingTicketId() || false }
            );

            this.state.groups = this.normalizeGroups(groups || []);
            this.syncCollapsedState();
            this.recalcAll();
            this.applyStoredSelections();
        } catch (error) {
            console.error("[WORKSHOP TICKET SELECTOR] load failed:", error);
            this.state.groups = [];
        } finally {
            this.state.isLoading = false;
        }
    }

    normalizeGroups(groups) {
        return (groups || []).map((group, groupIndex) => {
            const groupKey = group.groupKey || `group-${group.productId || 0}-${groupIndex}`;
            group._key = groupKey;
            group.lines = (group.lines || []).map((line, lineIndex) => {
                line._key = line.rowKey || `${groupKey}-${line.inputLineId || 0}-${lineIndex}`;
                line.groupKey = groupKey;
                line.isSelected = !!line.isSelected;
                return line;
            });
            return group;
        });
    }

    syncCollapsedState() {
        const next = {};
        for (const group of this.state.groups) {
            next[group._key] = this.state.collapsed[group._key] || false;
        }
        this.state.collapsed = next;
    }

    applyStoredSelections() {
        const root = this.getRootRecord();
        const raw = root?.data?.widget_selections;
        if (!raw || raw === "[]") return;

        try {
            const selections = JSON.parse(raw);
            if (!Array.isArray(selections)) return;
            const ids = new Set(
                selections
                    .map((item) => parseInt(item.inputLineId || 0, 10))
                    .filter(Boolean)
            );
            if (!ids.size) return;

            for (const group of this.state.groups) {
                for (const line of group.lines || []) {
                    line.isSelected = ids.has(parseInt(line.inputLineId || 0, 10));
                }
            }
            this.recalcAll();
        } catch (error) {
            console.warn("[WORKSHOP TICKET SELECTOR] invalid stored selections", error);
        }
    }

    writeSelectionsToRecord() {
        if (this._writeTimeout) {
            clearTimeout(this._writeTimeout);
        }
        this._writeTimeout = setTimeout(() => this.doWriteSelectionsToRecord(), 150);
    }

    doWriteSelectionsToRecord() {
        const selections = [];
        for (const group of this.state.groups) {
            for (const line of group.lines || []) {
                if (!line.isSelected) continue;
                selections.push({
                    inputLineId: line.inputLineId || 0,
                    lotId: line.lotId || 0,
                    lotName: line.lotName || "",
                    productId: line.productId || 0,
                    productName: line.productName || "",
                    qty: line.qty || 0,
                    areaSqm: line.areaSqm || 0,
                    locationId: line.locationId || 0,
                    locationName: line.locationName || "",
                });
            }
        }

        const root = this.getRootRecord();
        if (root?.update) {
            root.update({ widget_selections: JSON.stringify(selections) });
        }
    }

    toggleGroup(group) {
        this.state.collapsed[group._key] = !this.state.collapsed[group._key];
    }

    isCollapsed(group) {
        return !!this.state.collapsed[group._key];
    }

    expandAll() {
        for (const group of this.state.groups) {
            this.state.collapsed[group._key] = false;
        }
    }

    collapseAll() {
        for (const group of this.state.groups) {
            this.state.collapsed[group._key] = true;
        }
    }

    toggleLine(line) {
        line.isSelected = !line.isSelected;
        this.recalcGroupByLine(line);
        this.state.groups = [...this.state.groups];
        this.writeSelectionsToRecord();
    }

    selectAllInGroup(group) {
        for (const line of group.lines || []) {
            line.isSelected = true;
        }
        this.recalcGroup(group);
        this.state.groups = [...this.state.groups];
        this.writeSelectionsToRecord();
    }

    clearGroup(group) {
        for (const line of group.lines || []) {
            line.isSelected = false;
        }
        this.recalcGroup(group);
        this.state.groups = [...this.state.groups];
        this.writeSelectionsToRecord();
    }

    recalcGroupByLine(line) {
        const group = this.state.groups.find((g) => g._key === line.groupKey);
        if (group) this.recalcGroup(group);
    }

    recalcGroup(group) {
        group.selectedCount = 0;
        group.totalArea = 0;
        for (const line of group.lines || []) {
            if (line.isSelected) {
                group.selectedCount += 1;
                group.totalArea += line.areaSqm || 0;
            }
        }
    }

    recalcAll() {
        for (const group of this.state.groups) {
            this.recalcGroup(group);
        }
    }

    get totalSelectedCount() {
        let total = 0;
        for (const group of this.state.groups) total += group.selectedCount || 0;
        return total;
    }

    get totalSelectedArea() {
        let total = 0;
        for (const group of this.state.groups) total += group.totalArea || 0;
        return total;
    }

    formatNum(value, decimals = 2) {
        const num = parseFloat(value || 0);
        return Number.isFinite(num) ? num.toFixed(decimals) : (0).toFixed(decimals);
    }

    formatLocation(value) {
        if (!value) return "-";
        const parts = String(value).split("/").map((p) => p.trim()).filter(Boolean);
        if (!parts.length) return String(value);
        const index = parts.findIndex((p) => ["existencias", "stock", "inventario"].includes(p.toLowerCase()));
        if (index >= 0 && parts.slice(index + 1).length) {
            return parts.slice(index + 1).join("/");
        }
        return parts[parts.length - 1];
    }
}

registry.category("fields").add("workshop_ticket_selector", {
    component: WorkshopTicketSelector,
    displayName: "Selector de tickets de taller",
    supportedTypes: ["boolean"],
});
