/** @odoo-module **/

import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

export class WorkshopProgressLotSelector extends Component {
    static template = "stone_workshop.WorkshopProgressLotSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            groups: [],
            operationMode: "",
            isLoading: false,
            version: 0,
        });

        this._popupRoot = null;
        this._popupKeyHandler = null;
        this._popupObserver = null;
        this._loadToken = 0;

        onWillStart(async () => {
            await this.loadGroups(this.props);
        });

        onWillUpdateProps(async (nextProps) => {
            await this.loadGroups(nextProps);
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    notify(message, type = "info") {
        if (this.notification) {
            this.notification.add(message, { type, sticky: false });
        }
    }

    escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    extractId(value) {
        if (!value) return false;
        if (typeof value === "number") return value;
        if (Array.isArray(value)) return value[0] || false;
        if (typeof value === "object") return value.resId || value.id || value[0] || false;
        return false;
    }

    getRootRecord(props = this.props) {
        return props.record?.model?.root || props.record;
    }

    getOrderId(props = this.props) {
        const recordData = props.record?.data || {};
        const orderFromLine = this.extractId(recordData.order_id);
        if (orderFromLine) return orderFromLine;

        const root = this.getRootRecord(props);
        const rootData = root?.data || {};
        const rootModel = root?.model?.config?.resModel || root?.resModel || "";

        if (rootModel === "workshop.order") {
            return root?.resId || this.extractId(rootData.id) || false;
        }

        return this.extractId(rootData.order_id) || root?.resId || false;
    }

    getEditingLogId(props = this.props) {
        const rec = props.record;
        if (rec?.resId && typeof rec.resId === "number" && rec.resId > 0) return rec.resId;
        const dataId = rec?.data?.id;
        if (typeof dataId === "number" && dataId > 0) return dataId;
        return false;
    }

    getRecordKey(props = this.props) {
        return props.record?.id || props.record?.resId || props.record?.data?.id || "current";
    }

    canEdit() {
        return !this.props.readonly;
    }

    _normalizeIds(ids) {
        const result = [];
        for (const raw of ids || []) {
            const id = parseInt(raw, 10);
            if (id && !result.includes(id)) {
                result.push(id);
            }
        }
        return result;
    }

    getMany2ManyIds(value) {
        const ids = [];
        if (!value) return ids;

        if (Array.isArray(value.currentIds)) ids.push(...value.currentIds);
        if (Array.isArray(value.resIds)) ids.push(...value.resIds);

        if (Array.isArray(value.records)) {
            for (const record of value.records) {
                const rid = record.resId || record.data?.id || record.id;
                if (typeof rid === "number" && rid > 0) ids.push(rid);
            }
        }

        if (Array.isArray(value)) {
            for (const item of value) {
                if (typeof item === "number") {
                    ids.push(item);
                    continue;
                }
                if (Array.isArray(item)) {
                    if (item[0] === 6 && Array.isArray(item[2])) {
                        ids.push(...item[2]);
                    } else if (item[0] === 4 && item[1]) {
                        ids.push(item[1]);
                    }
                }
            }
        }

        return this._normalizeIds(ids);
    }

    getCurrentSelectedIds(props = this.props) {
        return this.getMany2ManyIds(props.record?.data?.input_line_ids);
    }

    getSiblingSelectedIds(props = this.props) {
        const root = this.getRootRecord(props);
        const logsField = root?.data?.progress_log_ids;
        const currentKey = this.getRecordKey(props);
        const currentResId = this.getEditingLogId(props);
        const ids = new Set();

        const records = Array.isArray(logsField?.records) ? logsField.records : [];
        for (const record of records) {
            const sameClientRecord = record.id && record.id === currentKey;
            const sameDbRecord = currentResId && record.resId === currentResId;
            if (sameClientRecord || sameDbRecord) continue;

            for (const id of this.getMany2ManyIds(record.data?.input_line_ids)) {
                ids.add(id);
            }
        }

        return ids;
    }

    normalizeGroups(groups) {
        const selectedIds = new Set(this.getCurrentSelectedIds());
        const siblingSelectedIds = this.getSiblingSelectedIds();

        return (groups || []).map((group, groupIndex) => {
            const groupKey = group.groupKey || `progress-group-${group.productId || 0}-${groupIndex}`;
            const normalized = {
                ...group,
                _key: groupKey,
                lines: [],
                lineCount: 0,
                selectedCount: 0,
                totalArea: 0,
            };

            for (const [lineIndex, line] of (group.lines || []).entries()) {
                const inputLineId = parseInt(line.inputLineId || 0, 10);
                const isSelected = selectedIds.has(inputLineId) || !!line.isSelected;

                if (siblingSelectedIds.has(inputLineId) && !isSelected) {
                    continue;
                }

                const cleanLine = {
                    ...line,
                    inputLineId,
                    _key: line.rowKey || `${groupKey}-${inputLineId || lineIndex}`,
                    groupKey,
                    isSelected,
                };

                normalized.lines.push(cleanLine);
                normalized.lineCount += 1;
                if (cleanLine.isSelected) {
                    normalized.selectedCount += 1;
                    normalized.totalArea += parseFloat(cleanLine.areaSqm || 0) || 0;
                }
            }

            return normalized;
        }).filter((group) => group.lineCount > 0 || group.selectedCount > 0);
    }

    async loadGroups(props = this.props) {
        const orderId = this.getOrderId(props);
        const token = ++this._loadToken;

        if (!orderId) {
            this.state.groups = [];
            this.state.operationMode = "";
            this.state.version += 1;
            return;
        }

        this.state.isLoading = true;
        try {
            const result = await this.orm.call(
                "workshop.order",
                "get_workshop_progress_selector_data",
                [[orderId]],
                {
                    current_input_line_ids: this.getCurrentSelectedIds(props),
                    editing_log_id: this.getEditingLogId(props) || false,
                }
            );

            if (token !== this._loadToken) return;

            const groups = Array.isArray(result) ? result : (result?.groups || []);
            this.state.operationMode = result?.operationMode || "";
            this.state.groups = this.normalizeGroups(groups);
            this.state.version += 1;
        } catch (error) {
            console.error("[WORKSHOP PROGRESS LOT SELECTOR] load failed:", error);
            this.state.groups = [];
            this.state.operationMode = "";
        } finally {
            if (token === this._loadToken) {
                this.state.isLoading = false;
            }
        }
    }

    get allLines() {
        void this.state.version;
        return this.state.groups.flatMap((group) => group.lines || []);
    }

    get selectedLines() {
        const ids = this.getCurrentSelectedIds();
        const lineById = new Map(this.allLines.map((line) => [line.inputLineId, line]));
        return ids.map((id) => lineById.get(id)).filter(Boolean);
    }

    get selectedCount() {
        return this.selectedLines.length;
    }

    get selectedArea() {
        return this.selectedLines.reduce((total, line) => total + (parseFloat(line.areaSqm || 0) || 0), 0);
    }

    get selectedPreviewText() {
        const lines = this.selectedLines.slice(0, 2).map((line) => line.lotName || "-");
        if (!lines.length) return "Sin placas";
        const remaining = this.selectedCount - lines.length;
        return remaining > 0 ? `${lines.join(", ")} +${remaining}` : lines.join(", ");
    }

    formatNum(value, decimals = 2) {
        const num = parseFloat(value || 0);
        return Number.isFinite(num) ? num.toFixed(decimals) : (0).toFixed(decimals);
    }

    formatDim(value) {
        const num = parseFloat(value || 0);
        if (!Number.isFinite(num) || !num) return "-";
        return num % 1 === 0 ? num.toFixed(0) : num.toFixed(2);
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

    destroyPopup() {
        if (this._popupObserver) {
            this._popupObserver.disconnect();
            this._popupObserver = null;
        }
        if (this._popupKeyHandler) {
            document.removeEventListener("keydown", this._popupKeyHandler);
            this._popupKeyHandler = null;
        }
        if (this._popupRoot) {
            this._popupRoot.remove();
            this._popupRoot = null;
        }
    }

    async openPopup(ev = null) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }

        if (!this.canEdit()) {
            this.notify("La bitácora ya no se puede modificar en este estado.", "warning");
            return;
        }

        await this.loadGroups(this.props);

        const orderId = this.getOrderId();
        if (!orderId) {
            this.notify("Guarda la orden antes de seleccionar placas en la bitácora.", "warning");
            return;
        }

        this.destroyPopup();
        this._popupRoot = document.createElement("div");
        this._popupRoot.className = "wpls-popup-root";
        document.body.appendChild(this._popupRoot);
        this.renderPopupDOM();
    }

    renderPopupDOM() {
        const root = this._popupRoot;
        const self = this;

        const popupState = {
            groups: JSON.parse(JSON.stringify(this.state.groups || [])),
            selectedIds: new Set(this.getCurrentSelectedIds()),
            collapsed: {},
            search: "",
        };

        for (const group of popupState.groups) {
            popupState.collapsed[group._key] = false;
        }

        const selectedArea = () => {
            let total = 0;
            for (const group of popupState.groups) {
                for (const line of group.lines || []) {
                    if (popupState.selectedIds.has(line.inputLineId)) {
                        total += parseFloat(line.areaSqm || 0) || 0;
                    }
                }
            }
            return total;
        };

        const selectedLines = () => {
            const lines = [];
            for (const group of popupState.groups) {
                for (const line of group.lines || []) {
                    if (popupState.selectedIds.has(line.inputLineId)) {
                        lines.push(line);
                    }
                }
            }
            return lines;
        };

        const matchesSearch = (line) => {
            const query = popupState.search.trim().toLowerCase();
            if (!query) return true;
            return [
                line.lotName,
                line.productName,
                line.blockName,
                line.tone,
                line.locationName,
            ].some((value) => String(value || "").toLowerCase().includes(query));
        };

        const recalcGroup = (group) => {
            group.selectedCount = 0;
            group.totalArea = 0;
            for (const line of group.lines || []) {
                line.isSelected = popupState.selectedIds.has(line.inputLineId);
                if (line.isSelected) {
                    group.selectedCount += 1;
                    group.totalArea += parseFloat(line.areaSqm || 0) || 0;
                }
            }
        };

        const recalcAll = () => {
            for (const group of popupState.groups) recalcGroup(group);
        };

        const confirm = async () => {
            const ids = Array.from(popupState.selectedIds).filter(Boolean);
            const values = {
                input_line_ids: [[6, 0, ids]],
            };

            if (["slab_finish", "rework"].includes(self.state.operationMode)) {
                values.area_sqm = selectedArea();
            }

            await self.props.record.update(values);
            self.destroyPopup();
            await self.loadGroups(self.props);
        };

        const render = () => {
            recalcAll();
            const count = popupState.selectedIds.size;
            const area = selectedArea();
            const selected = selectedLines();

            let selectedChips = "";
            for (const line of selected.slice(0, 8)) {
                selectedChips += `<span class="wpls-chip"><i class="fa fa-cube"></i>${self.escapeHtml(line.lotName || "-")}</span>`;
            }
            if (selected.length > 8) {
                selectedChips += `<span class="wpls-chip wpls-chip-more">+${selected.length - 8}</span>`;
            }

            let groupsHtml = "";
            for (const group of popupState.groups) {
                const visibleLines = (group.lines || []).filter(matchesSearch);
                if (!visibleLines.length && popupState.search) continue;

                const collapsed = !!popupState.collapsed[group._key];
                const visibleSelected = visibleLines.filter((line) => popupState.selectedIds.has(line.inputLineId)).length;
                let rows = "";

                for (const line of visibleLines) {
                    const isSelected = popupState.selectedIds.has(line.inputLineId);
                    rows += `
                        <tr data-line-id="${line.inputLineId}" class="${isSelected ? "is-selected" : ""}">
                            <td class="wpls-col-check">
                                <span class="wpls-check">${isSelected ? '<i class="fa fa-check"></i>' : ""}</span>
                            </td>
                            <td class="wpls-cell-lot">${self.escapeHtml(line.lotName || "-")}</td>
                            <td>${self.escapeHtml(line.blockName || "-")}</td>
                            <td>${self.escapeHtml(line.tone || "-")}</td>
                            <td class="text-end">${self.formatDim(line.widthCm)}</td>
                            <td class="text-end">${self.formatDim(line.heightCm)}</td>
                            <td class="text-end">${self.formatDim(line.thicknessCm)}</td>
                            <td class="text-end fw-bold">${self.formatNum(line.areaSqm, 4)}</td>
                            <td class="text-muted">${self.escapeHtml(self.formatLocation(line.locationName))}</td>
                        </tr>`;
                }

                groupsHtml += `
                    <div class="wpls-group ${collapsed ? "is-collapsed" : ""}" data-group-key="${self.escapeHtml(group._key)}">
                        <div class="wpls-group-header" data-group-toggle="${self.escapeHtml(group._key)}">
                            <span class="wpls-chevron"><i class="fa ${collapsed ? "fa-chevron-right" : "fa-chevron-down"}"></i></span>
                            <span class="wpls-product"><i class="fa fa-cube"></i>${self.escapeHtml(group.productName || "Producto")}</span>
                            <span class="wpls-pill">${visibleLines.length} visible(s)</span>
                            <span class="wpls-pill wpls-pill-selected">${visibleSelected} sel.</span>
                            <button type="button" class="wpls-mini-btn" data-select-group="${self.escapeHtml(group._key)}" title="Seleccionar visibles"><i class="fa fa-check-square-o"></i></button>
                            <button type="button" class="wpls-mini-btn wpls-mini-clear" data-clear-group="${self.escapeHtml(group._key)}" title="Limpiar grupo"><i class="fa fa-square-o"></i></button>
                        </div>
                        ${collapsed ? "" : `
                            <table class="wpls-table">
                                <thead>
                                    <tr>
                                        <th class="wpls-col-check">✓</th>
                                        <th>Lote</th>
                                        <th>Bloque</th>
                                        <th>Tono</th>
                                        <th class="text-end">Ancho</th>
                                        <th class="text-end">Alto</th>
                                        <th class="text-end">Esp.</th>
                                        <th class="text-end">m²</th>
                                        <th>Ubicación</th>
                                    </tr>
                                </thead>
                                <tbody>${rows}</tbody>
                            </table>`}
                    </div>`;
            }

            root.innerHTML = `
                <div class="wpls-overlay" id="wpls-overlay">
                    <div class="wpls-popup">
                        <div class="wpls-popup-header">
                            <div class="wpls-popup-title">
                                <i class="fa fa-th-large"></i>
                                <div>
                                    <strong>Seleccionar placas para esta corrida</strong>
                                    <span>Bitácora de taller · cada placa sólo puede estar en una corrida</span>
                                </div>
                            </div>
                            <div class="wpls-popup-actions">
                                <span class="wpls-popup-badge"><strong>${count}</strong> placa(s)</span>
                                <span class="wpls-popup-badge"><strong>${self.formatNum(area, 4)}</strong> m²</span>
                                <button type="button" class="wpls-btn wpls-btn-primary" id="wpls-confirm-top">
                                    <i class="fa fa-check"></i> Aplicar
                                </button>
                                <button type="button" class="wpls-btn wpls-btn-ghost" id="wpls-close">
                                    <i class="fa fa-times"></i>
                                </button>
                            </div>
                        </div>

                        <div class="wpls-popup-tools">
                            <div class="wpls-search">
                                <i class="fa fa-search"></i>
                                <input type="text" id="wpls-search-input" value="${self.escapeHtml(popupState.search)}" placeholder="Buscar por lote, producto, bloque, tono o ubicación..."/>
                            </div>
                            <button type="button" class="wpls-btn wpls-btn-soft" id="wpls-expand-all"><i class="fa fa-expand"></i> Expandir</button>
                            <button type="button" class="wpls-btn wpls-btn-soft" id="wpls-collapse-all"><i class="fa fa-compress"></i> Colapsar</button>
                        </div>

                        <div class="wpls-selected-bar">
                            <span class="wpls-selected-label">Selección actual</span>
                            <div class="wpls-selected-chips">${selectedChips || '<span class="wpls-empty-chip">Sin placas seleccionadas</span>'}</div>
                        </div>

                        <div class="wpls-popup-body" id="wpls-body">
                            ${groupsHtml || `
                                <div class="wpls-empty">
                                    <i class="fa fa-inbox"></i>
                                    <span>No hay placas disponibles con este filtro.</span>
                                </div>`}
                        </div>

                        <div class="wpls-popup-footer">
                            <span>Seleccionadas: <strong>${count}</strong> · <strong>${self.formatNum(area, 4)}</strong> m²</span>
                            <div>
                                <button type="button" class="wpls-btn wpls-btn-outline" id="wpls-cancel">Cancelar</button>
                                <button type="button" class="wpls-btn wpls-btn-primary" id="wpls-confirm-bottom">
                                    <i class="fa fa-check"></i> Aplicar selección
                                </button>
                            </div>
                        </div>
                    </div>
                </div>`;

            root.querySelector("#wpls-overlay")?.addEventListener("click", (event) => {
                if (event.target.id === "wpls-overlay") self.destroyPopup();
            });
            root.querySelector("#wpls-close")?.addEventListener("click", () => self.destroyPopup());
            root.querySelector("#wpls-cancel")?.addEventListener("click", () => self.destroyPopup());
            root.querySelector("#wpls-confirm-top")?.addEventListener("click", confirm);
            root.querySelector("#wpls-confirm-bottom")?.addEventListener("click", confirm);

            const searchInput = root.querySelector("#wpls-search-input");
            if (searchInput) {
                searchInput.focus();
                searchInput.setSelectionRange(searchInput.value.length, searchInput.value.length);
                searchInput.addEventListener("input", () => {
                    popupState.search = searchInput.value || "";
                    render();
                });
            }

            root.querySelector("#wpls-expand-all")?.addEventListener("click", () => {
                for (const key of Object.keys(popupState.collapsed)) popupState.collapsed[key] = false;
                render();
            });
            root.querySelector("#wpls-collapse-all")?.addEventListener("click", () => {
                for (const key of Object.keys(popupState.collapsed)) popupState.collapsed[key] = true;
                render();
            });

            root.querySelectorAll("[data-group-toggle]").forEach((el) => {
                el.addEventListener("click", () => {
                    const key = el.dataset.groupToggle;
                    popupState.collapsed[key] = !popupState.collapsed[key];
                    render();
                });
            });

            root.querySelectorAll("[data-select-group]").forEach((button) => {
                button.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const key = button.dataset.selectGroup;
                    const group = popupState.groups.find((g) => g._key === key);
                    for (const line of (group?.lines || []).filter(matchesSearch)) {
                        popupState.selectedIds.add(line.inputLineId);
                    }
                    render();
                });
            });

            root.querySelectorAll("[data-clear-group]").forEach((button) => {
                button.addEventListener("click", (event) => {
                    event.stopPropagation();
                    const key = button.dataset.clearGroup;
                    const group = popupState.groups.find((g) => g._key === key);
                    for (const line of (group?.lines || []).filter(matchesSearch)) {
                        popupState.selectedIds.delete(line.inputLineId);
                    }
                    render();
                });
            });

            root.querySelectorAll("tr[data-line-id]").forEach((row) => {
                row.addEventListener("click", () => {
                    const id = parseInt(row.dataset.lineId, 10);
                    if (!id) return;
                    if (popupState.selectedIds.has(id)) {
                        popupState.selectedIds.delete(id);
                    } else {
                        popupState.selectedIds.add(id);
                    }
                    render();
                });
            });
        };

        this._popupKeyHandler = (event) => {
            if (event.key === "Escape") this.destroyPopup();
        };
        document.addEventListener("keydown", this._popupKeyHandler);

        render();
    }
}

registry.category("fields").add("workshop_progress_lot_selector", {
    component: WorkshopProgressLotSelector,
    displayName: "Selector visual de placas de bitácora",
    supportedTypes: ["boolean"],
});
