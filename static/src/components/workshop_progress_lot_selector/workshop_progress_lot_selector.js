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
        // En listas editables, `props.readonly` es true mientras la fila no
        // esté en modo edición, así que no sirve como criterio para deshabilitar
        // el botón: bloquearía el click justo antes de entrar a edición.
        // Mejor leer el estado de la orden raíz; sólo bloqueamos cuando la
        // orden está cerrada o cancelada.
        const root = this.getRootRecord();
        const state = root?.data?.state;
        if (state && ['done', 'cancel'].includes(state)) return false;
        return true;
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

    // Lee la lista de consumos de la corrida actual (One2many a workshop.progress.log.line).
    // Devuelve [{inputLineId, consumedSqm, recordKey, resId}]
    getCurrentConsumptions(props = this.props) {
        const list = props.record?.data?.consumption_line_ids;
        const records = Array.isArray(list?.records) ? list.records : [];
        const result = [];
        for (const child of records) {
            const inputLineId = this.extractId(child.data?.input_line_id);
            const consumed = parseFloat(child.data?.consumed_sqm || 0) || 0;
            if (inputLineId) {
                result.push({
                    inputLineId,
                    consumedSqm: consumed,
                    recordKey: child.id || null,
                    resId: child.resId && child.resId > 0 ? child.resId : null,
                });
            }
        }
        return result;
    }

    getCurrentSelectedIds(props = this.props) {
        return this._normalizeIds(
            this.getCurrentConsumptions(props)
                .filter((c) => c.consumedSqm > 0)
                .map((c) => c.inputLineId)
        );
    }

    getCurrentConsumptionMap(props = this.props) {
        const map = {};
        for (const c of this.getCurrentConsumptions(props)) {
            map[c.inputLineId] = c.consumedSqm;
        }
        return map;
    }

    // Consumos de las demás corridas (hermanas) por placa, para descontarlos del remanente disponible.
    getSiblingConsumedById(props = this.props) {
        const root = this.getRootRecord(props);
        const logsField = root?.data?.progress_log_ids;
        const currentKey = this.getRecordKey(props);
        const currentResId = this.getEditingLogId(props);
        const map = {};

        const records = Array.isArray(logsField?.records) ? logsField.records : [];
        for (const record of records) {
            const sameClientRecord = record.id && record.id === currentKey;
            const sameDbRecord = currentResId && record.resId === currentResId;
            if (sameClientRecord || sameDbRecord) continue;

            const consumptionList = record.data?.consumption_line_ids;
            const consumptionRecords = Array.isArray(consumptionList?.records) ? consumptionList.records : [];
            for (const consChild of consumptionRecords) {
                const inputLineId = this.extractId(consChild.data?.input_line_id);
                const consumed = parseFloat(consChild.data?.consumed_sqm || 0) || 0;
                if (inputLineId && consumed > 0) {
                    map[inputLineId] = (map[inputLineId] || 0) + consumed;
                }
            }
        }

        return map;
    }

    normalizeGroups(groups) {
        const currentConsumptionMap = this.getCurrentConsumptionMap();
        const siblingConsumed = this.getSiblingConsumedById();

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
                const consumedHere = parseFloat(
                    currentConsumptionMap[inputLineId] !== undefined
                        ? currentConsumptionMap[inputLineId]
                        : line.consumedInThisLog || 0
                ) || 0;
                const totalArea = parseFloat(line.areaSqm || 0) || 0;
                // Consumo en OTRAS corridas: tomamos el mayor entre lo que ve el
                // cliente (todas las corridas hermanas, guardadas o no) y lo que
                // ya descontó el servidor (corridas guardadas). El max evita que
                // una placa consumida al total reaparezca disponible en otra línea.
                const siblingFromClient = parseFloat(siblingConsumed[inputLineId] || 0) || 0;
                const siblingFromServer = Math.max(0, totalArea - parseFloat(line.remainingSqm || 0));
                const usedOther = Math.max(siblingFromClient, siblingFromServer);
                const remaining = Math.max(0, totalArea - usedOther);
                const isSelected = consumedHere > 0;

                if (remaining <= 0.0001 && !isSelected) {
                    continue;
                }

                const cleanLine = {
                    ...line,
                    inputLineId,
                    _key: line.rowKey || `${groupKey}-${inputLineId || lineIndex}`,
                    groupKey,
                    isSelected,
                    consumedHere,
                    remainingSqm: remaining,
                };

                normalized.lines.push(cleanLine);
                normalized.lineCount += 1;
                if (isSelected) {
                    normalized.selectedCount += 1;
                    normalized.totalArea += consumedHere;
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
                    current_consumptions: this.getCurrentConsumptionMap(props),
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
        return this.allLines.filter((line) => line.consumedHere > 0);
    }

    get selectedCount() {
        return this.selectedLines.length;
    }

    get selectedArea() {
        return this.selectedLines.reduce((total, line) => total + (parseFloat(line.consumedHere || 0) || 0), 0);
    }

    get selectedPreviewText() {
        const lines = this.selectedLines.slice(0, 2).map((line) => {
            const consumed = parseFloat(line.consumedHere || 0) || 0;
            return `${line.lotName || "-"} (${consumed.toFixed(2)} m²)`;
        });
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

    async openPopup() {
        // No detenemos la propagación del click: en listas editables eso es
        // lo que dispara que Odoo ponga la fila en modo edición. Sin ese
        // paso, `record.update(...)` después no marca la fila como sucia.
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
        // Aislamos los clicks del popup del resto de la página. Sin esto, un
        // click adentro burbujea al document, donde el controlador de la lista
        // editable lo interpreta como "click fuera de la fila" y desmonta el
        // widget → `onWillUnmount` → `destroyPopup`, cerrando el popup justo
        // cuando el usuario intenta seleccionar un lote.
        this._popupRoot.addEventListener("click", (event) => {
            event.stopPropagation();
        });
        this._popupRoot.addEventListener("mousedown", (event) => {
            event.stopPropagation();
        });
        document.body.appendChild(this._popupRoot);
        this.renderPopupDOM();
    }

    renderPopupDOM() {
        const root = this._popupRoot;
        const self = this;

        // popupState.consumed: Map<inputLineId, consumed_sqm capturado en esta corrida>
        const popupState = {
            groups: JSON.parse(JSON.stringify(this.state.groups || [])),
            consumed: new Map(),
            collapsed: {},
            search: "",
        };

        for (const group of popupState.groups) {
            popupState.collapsed[group._key] = false;
            for (const line of group.lines || []) {
                const consumed = parseFloat(line.consumedHere || 0) || 0;
                if (consumed > 0) {
                    popupState.consumed.set(line.inputLineId, consumed);
                }
            }
        }

        const selectedArea = () => {
            let total = 0;
            for (const [, value] of popupState.consumed) {
                total += parseFloat(value || 0) || 0;
            }
            return total;
        };

        const selectedLinesList = () => {
            const lines = [];
            for (const group of popupState.groups) {
                for (const line of group.lines || []) {
                    if (popupState.consumed.has(line.inputLineId)) {
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
                const consumed = parseFloat(popupState.consumed.get(line.inputLineId) || 0) || 0;
                line.consumedHere = consumed;
                line.isSelected = consumed > 0;
                if (line.isSelected) {
                    group.selectedCount += 1;
                    group.totalArea += consumed;
                }
            }
        };

        const recalcAll = () => {
            for (const group of popupState.groups) recalcGroup(group);
        };

        const toggleLine = (line) => {
            if (popupState.consumed.has(line.inputLineId)) {
                popupState.consumed.delete(line.inputLineId);
            } else {
                // Default al remanente disponible.
                const remaining = parseFloat(line.remainingSqm || 0) || 0;
                popupState.consumed.set(line.inputLineId, remaining > 0 ? remaining : 0);
            }
        };

        const setConsumed = (line, value) => {
            const num = parseFloat(value);
            if (!Number.isFinite(num) || num <= 0) {
                popupState.consumed.delete(line.inputLineId);
                return;
            }
            const remaining = parseFloat(line.remainingSqm || 0) || 0;
            // Topear al remanente disponible (no se puede consumir más de lo que hay).
            const capped = remaining > 0 ? Math.min(num, remaining) : num;
            popupState.consumed.set(line.inputLineId, capped);
        };

        const confirm = async () => {
            const items = selectedLinesList()
                .map((line) => ({
                    inputLineId: parseInt(line.inputLineId || 0, 10),
                    lotName: line.lotName || "",
                    consumedSqm: parseFloat(popupState.consumed.get(line.inputLineId) || 0) || 0,
                }))
                .filter((item) => item.consumedSqm > 0 && item.inputLineId);

            const totalConsumed = items.reduce((acc, item) => acc + item.consumedSqm, 0);
            const isOneToOne = ["slab_finish", "rework"].includes(self.state.operationMode);
            const currentArea = parseFloat(self.props.record?.data?.area_sqm || 0) || 0;

            // Construir One2many commands para reemplazar consumption_line_ids.
            // [5, 0, 0] = vaciar; [0, 0, vals] = crear nueva fila virtual.
            // IMPORTANTE: en record.update, los Many2one deben ir como [id, nombre]
            // (no como id pelón); si no, el web framework descarta el valor y la
            // fila se crea con input_line_id = NULL (rompe la constraint NOT NULL).
            const commands = [[5, 0, 0]];
            for (const item of items) {
                commands.push([0, 0, {
                    input_line_id: [item.inputLineId, item.lotName || String(item.inputLineId)],
                    consumed_sqm: item.consumedSqm,
                }]);
            }

            const values = {
                consumption_line_ids: commands,
            };

            if (isOneToOne) {
                values.area_sqm = totalConsumed;
            } else if (currentArea > totalConsumed + 0.0001) {
                values.area_sqm = totalConsumed;
            } else if (currentArea <= 0 && totalConsumed > 0) {
                values.area_sqm = totalConsumed;
            }

            await self.props.record.update(values);
            self.destroyPopup();
            await self.loadGroups(self.props);
        };

        const render = () => {
            recalcAll();
            const count = Array.from(popupState.consumed.values()).filter((v) => (parseFloat(v || 0) || 0) > 0).length;
            const area = selectedArea();
            const selected = selectedLinesList();

            let selectedChips = "";
            for (const line of selected.slice(0, 8)) {
                const consumed = parseFloat(popupState.consumed.get(line.inputLineId) || 0) || 0;
                selectedChips += `<span class="wpls-chip"><i class="fa fa-cube"></i>${self.escapeHtml(line.lotName || "-")} · ${consumed.toFixed(2)} m²</span>`;
            }
            if (selected.length > 8) {
                selectedChips += `<span class="wpls-chip wpls-chip-more">+${selected.length - 8}</span>`;
            }

            let groupsHtml = "";
            for (const group of popupState.groups) {
                const visibleLines = (group.lines || []).filter(matchesSearch);
                if (!visibleLines.length && popupState.search) continue;

                const collapsed = !!popupState.collapsed[group._key];
                const visibleSelected = visibleLines.filter((line) => popupState.consumed.has(line.inputLineId)).length;
                let rows = "";

                for (const line of visibleLines) {
                    const consumed = parseFloat(popupState.consumed.get(line.inputLineId) || 0) || 0;
                    const isSelected = consumed > 0;
                    const remaining = parseFloat(line.remainingSqm || 0) || 0;
                    const total = parseFloat(line.areaSqm || 0) || 0;
                    rows += `
                        <tr data-line-id="${line.inputLineId}" class="${isSelected ? "is-selected" : ""}">
                            <td class="wpls-col-check" data-action="toggle">
                                <span class="wpls-check">${isSelected ? '<i class="fa fa-check"></i>' : ""}</span>
                            </td>
                            <td class="wpls-cell-lot" data-action="toggle">${self.escapeHtml(line.lotName || "-")}</td>
                            <td data-action="toggle">${self.escapeHtml(line.blockName || "-")}</td>
                            <td data-action="toggle">${self.escapeHtml(line.tone || "-")}</td>
                            <td class="text-end" data-action="toggle">${self.formatDim(line.widthCm)}</td>
                            <td class="text-end" data-action="toggle">${self.formatDim(line.heightCm)}</td>
                            <td class="text-end" data-action="toggle">${self.formatDim(line.thicknessCm)}</td>
                            <td class="text-end" data-action="toggle">${total.toFixed(4)}</td>
                            <td class="text-end fw-bold" data-action="toggle">${remaining.toFixed(4)}</td>
                            <td class="wpls-cell-consumed">
                                <input type="number"
                                       class="wpls-consumed-input"
                                       data-line-input="${line.inputLineId}"
                                       min="0"
                                       step="0.0001"
                                       value="${isSelected ? consumed.toFixed(4) : ""}"
                                       placeholder="${remaining.toFixed(4)}"/>
                            </td>
                            <td class="text-muted" data-action="toggle">${self.escapeHtml(self.formatLocation(line.locationName))}</td>
                        </tr>`;
                }

                groupsHtml += `
                    <div class="wpls-group ${collapsed ? "is-collapsed" : ""}" data-group-key="${self.escapeHtml(group._key)}">
                        <div class="wpls-group-header" data-group-toggle="${self.escapeHtml(group._key)}">
                            <span class="wpls-chevron"><i class="fa ${collapsed ? "fa-chevron-right" : "fa-chevron-down"}"></i></span>
                            <span class="wpls-product"><i class="fa fa-cube"></i>${self.escapeHtml(group.productName || "Producto")}</span>
                            <span class="wpls-pill">${visibleLines.length} visible(s)</span>
                            <span class="wpls-pill wpls-pill-selected">${visibleSelected} sel.</span>
                            <button type="button" class="wpls-mini-btn" data-select-group="${self.escapeHtml(group._key)}" title="Marcar todas con su remanente"><i class="fa fa-check-square-o"></i></button>
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
                                        <th class="text-end">m² total</th>
                                        <th class="text-end">m² disponible</th>
                                        <th class="text-end">m² a consumir</th>
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
                                    <strong>Capturar consumo de placas en esta corrida</strong>
                                    <span>Bitácora de taller · admite consumos parciales. Los m² remanentes seguirán disponibles para la siguiente corrida.</span>
                                </div>
                            </div>
                            <div class="wpls-popup-actions">
                                <span class="wpls-popup-badge"><strong>${count}</strong> placa(s)</span>
                                <span class="wpls-popup-badge"><strong>${self.formatNum(area, 4)}</strong> m² consumidos</span>
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
                                    <span>No hay placas con remanente disponible.</span>
                                </div>`}
                        </div>

                        <div class="wpls-popup-footer">
                            <span>Consumido en esta corrida: <strong>${count}</strong> placa(s) · <strong>${self.formatNum(area, 4)}</strong> m²</span>
                            <div>
                                <button type="button" class="wpls-btn wpls-btn-outline" id="wpls-cancel">Cancelar</button>
                                <button type="button" class="wpls-btn wpls-btn-primary" id="wpls-confirm-bottom">
                                    <i class="fa fa-check"></i> Aplicar
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
                        const remaining = parseFloat(line.remainingSqm || 0) || 0;
                        if (remaining > 0) {
                            popupState.consumed.set(line.inputLineId, remaining);
                        }
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
                        popupState.consumed.delete(line.inputLineId);
                    }
                    render();
                });
            });

            root.querySelectorAll("tr[data-line-id]").forEach((row) => {
                row.addEventListener("click", (event) => {
                    // Si el click vino del input de cantidad, no togglear.
                    const target = event.target;
                    if (target && target.closest && target.closest(".wpls-consumed-input, .wpls-cell-consumed")) {
                        return;
                    }
                    const id = parseInt(row.dataset.lineId, 10);
                    if (!id) return;
                    const line = popupState.groups
                        .flatMap((g) => g.lines || [])
                        .find((l) => l.inputLineId === id);
                    if (!line) return;
                    toggleLine(line);
                    render();
                });
            });

            root.querySelectorAll(".wpls-consumed-input").forEach((input) => {
                input.addEventListener("click", (event) => {
                    event.stopPropagation();
                });
                input.addEventListener("input", (event) => {
                    event.stopPropagation();
                    const id = parseInt(input.dataset.lineInput, 10);
                    const line = popupState.groups
                        .flatMap((g) => g.lines || [])
                        .find((l) => l.inputLineId === id);
                    if (!line) return;
                    setConsumed(line, input.value);
                });
                input.addEventListener("change", (event) => {
                    event.stopPropagation();
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
