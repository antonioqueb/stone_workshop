/** @odoo-module **/

import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

const STATE_LABELS = {
    pending: "Pendiente",
    reserved_for_workshop: "Reservada",
    sent_to_workshop: "En taller",
    in_progress: "En proceso",
    partial_done: "Parcial",
    done: "Terminada",
    rejected: "Rechazada",
    damaged: "Dañada",
    cancelled: "Cancelada",
};

export class WorkshopLotSelector extends Component {
    static template = "stone_workshop.WorkshopLotSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            version: 0,
            savedRows: [],
            savedRowsLoaded: false,
            savedRowsOrderId: false,
        });

        this._popupRoot = null;
        this._popupKeyHandler = null;
        this._popupObserver = null;

        onWillStart(async () => {
            await this._loadSavedRowsFromServer();
        });

        onWillUpdateProps(async (nextProps) => {
            const currentOrderId = this.getOrderId(this.props);
            const nextOrderId = this.getOrderId(nextProps);

            if (currentOrderId !== nextOrderId) {
                await this._loadSavedRowsFromServer(nextProps);
            }

            this.state.version += 1;
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    _notify(message, type = "info") {
        if (this.notification) {
            this.notification.add(message, { type, sticky: false });
        }
    }

    _escapeHtml(value) {
        if (value === null || value === undefined) return "";
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    _extractId(value) {
        if (!value) return false;
        if (typeof value === "number") return value;
        if (Array.isArray(value)) return value[0] || false;
        if (typeof value === "object") {
            return value.resId || value.id || value[0] || false;
        }
        return false;
    }

    _extractName(value) {
        if (!value) return "";
        if (Array.isArray(value)) return value[1] || "";
        if (typeof value === "object") {
            return value.display_name || value.name || value.value || "";
        }
        return String(value || "");
    }

    _extractNumber(value) {
        if (value === null || value === undefined || value === false) return 0;
        if (typeof value === "number") return value;
        if (typeof value === "string") return parseFloat(value.replace(",", ".")) || 0;
        if (typeof value === "object") {
            if ("value" in value) return this._extractNumber(value.value);
            if ("raw_value" in value) return this._extractNumber(value.raw_value);
        }
        return 0;
    }

    _getOrderState(props = this.props) {
        const state = props.record.data.state;
        if (!state) return "draft";
        if (typeof state === "string") return state;
        if (typeof state === "object") return state.value || state.raw_value || state.name || "draft";
        return String(state || "draft");
    }

    canEdit() {
        const state = this._getOrderState();
        return !this.props.readonly && ["draft", "validated"].includes(state);
    }

    getOrderId(props = this.props) {
        const id = props.record.resId || props.record.data.id || false;
        return typeof id === "number" && id > 0 ? id : false;
    }

    getProductId() {
        const data = this.props.record.data || {};
        const selectedProduct = this._extractId(data.input_product_id);
        if (selectedProduct) return selectedProduct;

        const firstRow = this.selectedRows[0];
        return firstRow ? firstRow.product_id : false;
    }

    getProductName() {
        const data = this.props.record.data || {};
        const selectedProductName = this._extractName(data.input_product_id);
        if (selectedProductName) return selectedProductName;

        const firstRow = this.selectedRows[0];
        return firstRow ? firstRow.product_name : "";
    }

    getLocationSrcId() {
        return this._extractId(this.props.record.data.location_src_id);
    }

    _getX2ManyRecords(fieldName) {
        const value = this.props.record.data[fieldName];
        if (!value) return [];
        if (Array.isArray(value.records)) return value.records;
        if (Array.isArray(value)) return value;
        return [];
    }

    _hasOutputLines() {
        return this._getX2ManyRecords("output_line_ids").length > 0;
    }

    _effectiveArea(row) {
        const area = this._extractNumber(row && row.area_sqm);
        const qty = this._extractNumber(row && row.qty_in);

        // Blindaje visual para líneas creadas con el bug metro/centímetro:
        // si área_sqm quedó diminuta, pero Cant. sí trae los m² reales, mostramos Cant. como área.
        if (qty > 0 && (!area || area < qty * 0.25)) {
            return qty;
        }

        return area || qty || 0;
    }

    _serverRowToDisplayRow(row) {
        const state = row.state || "pending";
        const qtyIn = this._extractNumber(row.qty_in);
        const areaSqm = this._effectiveArea({ area_sqm: row.area_sqm, qty_in: qtyIn });
        return {
            key: row.id,
            id: row.id,
            lot_id: row.lot_id ? row.lot_id[0] : false,
            lot_name: row.lot_id ? row.lot_id[1] : "-",
            product_id: row.product_id ? row.product_id[0] : false,
            product_name: row.product_id ? row.product_id[1] : "-",
            qty_in: qtyIn,
            area_sqm: areaSqm,
            width_cm: this._extractNumber(row.width_cm),
            height_cm: this._extractNumber(row.height_cm),
            thickness_cm: this._extractNumber(row.thickness_cm),
            block_name: row.block_name || "",
            tone: row.tone || "",
            location_name: row.location_id ? String(row.location_id[1]).split("/").pop() : "",
            state,
            state_label: STATE_LABELS[state] || state,
        };
    }

    async _loadSavedRowsFromServer(props = this.props) {
        const orderId = this.getOrderId(props);

        if (!orderId) {
            this.state.savedRows = [];
            this.state.savedRowsLoaded = false;
            this.state.savedRowsOrderId = false;
            return;
        }

        try {
            const rows = await this.orm.searchRead(
                "workshop.input.line",
                [["order_id", "=", orderId], ["state", "!=", "cancelled"]],
                [
                    "id",
                    "sequence",
                    "material_type",
                    "product_id",
                    "lot_id",
                    "qty_in",
                    "area_sqm",
                    "width_cm",
                    "height_cm",
                    "thickness_cm",
                    "pieces",
                    "block_name",
                    "tone",
                    "current_finish",
                    "location_id",
                    "reserved_origin",
                    "state",
                ],
                { order: "sequence, id" }
            );

            this.state.savedRows = (rows || [])
                .map((row) => this._serverRowToDisplayRow(row))
                .filter((row) => row.lot_id);

            this.state.savedRowsLoaded = true;
            this.state.savedRowsOrderId = orderId;
        } catch (error) {
            console.warn("[WORKSHOP LOT SELECTOR] No se pudieron cargar entradas guardadas:", error);
            this.state.savedRows = [];
            this.state.savedRowsLoaded = false;
            this.state.savedRowsOrderId = false;
        }
    }

    _shouldUseSavedRows() {
        const orderId = this.getOrderId();
        return (
            orderId &&
            this.state.savedRowsLoaded &&
            this.state.savedRowsOrderId === orderId
        );
    }

    get selectedRows() {
        void this.state.version;

        if (this._shouldUseSavedRows()) {
            return this.state.savedRows || [];
        }

        const records = this._getX2ManyRecords("input_line_ids");

        return records.map((record, index) => {
            const data = record.data || record;
            const lotId = this._extractId(data.lot_id);
            const productId = this._extractId(data.product_id);
            const locationName = this._extractName(data.location_id);
            const state = data.state || "pending";
            const qtyIn = this._extractNumber(data.qty_in);
            const areaSqm = this._effectiveArea({ area_sqm: data.area_sqm, qty_in: qtyIn });

            return {
                key: record.id || record.resId || lotId || index,
                lot_id: lotId,
                lot_name: this._extractName(data.lot_id) || "-",
                product_id: productId,
                product_name: this._extractName(data.product_id) || "-",
                qty_in: qtyIn,
                area_sqm: areaSqm,
                width_cm: this._extractNumber(data.width_cm),
                height_cm: this._extractNumber(data.height_cm),
                thickness_cm: this._extractNumber(data.thickness_cm),
                block_name: data.block_name || "",
                tone: data.tone || "",
                location_name: locationName ? locationName.split("/").pop() : "",
                state,
                state_label: STATE_LABELS[state] || state,
            };
        }).filter((row) => row.lot_id);
    }

    get selectedArea() {
        return this.selectedRows.reduce((total, row) => {
            return total + this._effectiveArea(row);
        }, 0);
    }

    formatNum(value) {
        const num = parseFloat(value || 0);
        return Number.isFinite(num) ? num.toFixed(2) : "0.00";
    }

    formatDim(value) {
        const num = parseFloat(value || 0);
        if (!Number.isFinite(num) || !num) return "-";
        return num % 1 === 0 ? num.toFixed(0) : num.toFixed(2);
    }

    _getCurrentLotIds() {
        return this.selectedRows.map((row) => row.lot_id).filter(Boolean);
    }

    async removeLot(lotId, ev = null) {
        if (ev) ev.stopPropagation();

        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const nextLotIds = this._getCurrentLotIds().filter((id) => id !== lotId);
        await this._rebuildInputLines(nextLotIds);
    }

    async _readDisplayNameMap(model, ids) {
        const cleanIds = Array.from(new Set((ids || []).map((id) => parseInt(id, 10)).filter(Boolean)));
        const result = new Map();

        if (!cleanIds.length) return result;

        try {
            const rows = await this.orm.read(model, cleanIds, ["display_name"]);
            for (const row of rows || []) {
                result.set(row.id, row.display_name || String(row.id));
            }
        } catch (error) {
            console.warn(`[WORKSHOP LOT SELECTOR] No se pudo leer display_name de ${model}:`, error);
            for (const id of cleanIds) {
                result.set(id, String(id));
            }
        }

        return result;
    }

    async _buildRecordUpdateNameMaps(lineVals) {
        const productIds = [];
        const lotIds = [];
        const locationIds = [];

        for (const vals of lineVals || []) {
            const productId = this._extractId(vals.product_id);
            const lotId = this._extractId(vals.lot_id);
            const locationId = this._extractId(vals.location_id);

            if (productId) productIds.push(productId);
            if (lotId) lotIds.push(lotId);
            if (locationId) locationIds.push(locationId);
        }

        const [productNames, lotNames, locationNames] = await Promise.all([
            this._readDisplayNameMap("product.product", productIds),
            this._readDisplayNameMap("stock.lot", lotIds),
            this._readDisplayNameMap("stock.location", locationIds),
        ]);

        return { productNames, lotNames, locationNames };
    }

    _toRecordMany2OneValue(value, nameMap, fallbackName = "") {
        const id = this._extractId(value);
        if (!id) return false;
        return [id, nameMap.get(id) || fallbackName || String(id)];
    }

    _normalizeInputLineValsForRecordUpdate(vals, nameMaps) {
        const cleanVals = { ...(vals || {}) };

        const productId = this._extractId(cleanVals.product_id);
        const lotId = this._extractId(cleanVals.lot_id);
        const locationId = this._extractId(cleanVals.location_id);

        if (!lotId) {
            return null;
        }

        if (productId) {
            cleanVals.product_id = this._toRecordMany2OneValue(
                productId,
                nameMaps.productNames,
                this.getProductName() || String(productId)
            );
        }

        cleanVals.lot_id = this._toRecordMany2OneValue(
            lotId,
            nameMaps.lotNames,
            String(lotId)
        );

        if (locationId) {
            cleanVals.location_id = this._toRecordMany2OneValue(
                locationId,
                nameMaps.locationNames,
                String(locationId)
            );
        } else if ("location_id" in cleanVals) {
            cleanVals.location_id = false;
        }

        return cleanVals;
    }

    _normalizeInputLineValsForServerWrite(vals) {
        const productId = this._extractId(vals.product_id);
        const lotId = this._extractId(vals.lot_id);
        const locationId = this._extractId(vals.location_id);

        if (!productId || !lotId) {
            return null;
        }

        const cleanVals = {
            sequence: vals.sequence || 10,
            material_type: vals.material_type || "slab",
            product_id: productId,
            lot_id: lotId,
            qty_in: vals.qty_in || 1.0,
            area_sqm: vals.area_sqm || vals.qty_in || 0.0,
            width_cm: vals.width_cm || 0.0,
            height_cm: vals.height_cm || 0.0,
            thickness_cm: vals.thickness_cm || 0.0,
            pieces: vals.pieces || 1,
            block_name: vals.block_name || false,
            tone: vals.tone || false,
            current_finish: vals.current_finish || false,
            reserved_origin: vals.reserved_origin || false,
            state: vals.state || "pending",
        };

        if (locationId) {
            cleanVals.location_id = locationId;
        }

        return cleanVals;
    }

    async _prepareLineVals(cleanLotIds, productId) {
        if (!cleanLotIds.length) return [];

        return await this.orm.call(
            "workshop.order",
            "prepare_input_line_vals_from_lots",
            [],
            {
                product_id: productId,
                lot_ids: cleanLotIds,
                location_id: this.getLocationSrcId() || false,
            }
        );
    }

    async _writeInputLinesDirectly(orderId, lineVals) {
        const serverVals = [];

        for (const vals of lineVals || []) {
            const clean = this._normalizeInputLineValsForServerWrite(vals);
            if (clean) {
                serverVals.push(clean);
            }
        }

        const updateVals = {
            input_line_ids: [
                [5, 0, 0],
                ...serverVals.map((vals) => [0, 0, vals]),
            ],
        };

        if (this._hasOutputLines()) {
            updateVals.output_line_ids = [[5, 0, 0]];
        }

        await this.orm.write("workshop.order", [orderId], updateVals);

        await this._loadSavedRowsFromServer();
        this.state.version += 1;

        if (this._hasOutputLines()) {
            this._notify("Se actualizaron entradas y se limpiaron salidas esperadas para evitar desajustes.", "warning");
        }
    }

    async _updateInputLinesInUnsavedRecord(lineVals) {
        const nameMaps = await this._buildRecordUpdateNameMaps(lineVals);
        const normalizedLineVals = [];

        for (const vals of lineVals || []) {
            const normalized = this._normalizeInputLineValsForRecordUpdate(vals, nameMaps);
            if (normalized) {
                normalizedLineVals.push(normalized);
            }
        }

        if ((lineVals || []).length && !normalizedLineVals.length) {
            this._notify(
                "No se pudo preparar ninguna línea válida con lote. Revisa que los lotes seleccionados existan y tengan producto.",
                "danger"
            );
            return;
        }

        if ((lineVals || []).length !== normalizedLineVals.length) {
            this._notify(
                "Se omitieron una o más líneas sin lote válido para evitar guardar entradas incompletas.",
                "warning"
            );
        }

        const updateVals = {
            input_line_ids: [
                [5, 0, 0],
                ...normalizedLineVals.map((vals) => [0, 0, vals]),
            ],
        };

        if (this._hasOutputLines()) {
            updateVals.output_line_ids = [[5, 0, 0]];
        }

        await this.props.record.update(updateVals);
        this.state.savedRowsLoaded = false;
        this.state.savedRows = [];
        this.state.version += 1;

        if (this._hasOutputLines()) {
            this._notify("Se actualizaron entradas y se limpiaron salidas esperadas para evitar desajustes.", "warning");
        }
    }

    async _rebuildInputLines(lotIds) {
        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const cleanLotIds = Array.from(
            new Set((lotIds || []).map((id) => parseInt(id, 10)).filter(Boolean))
        );

        const productId = this.getProductId();

        if (!productId && cleanLotIds.length) {
            this._notify("Selecciona un producto de entrada antes de agregar lotes.", "warning");
            return;
        }

        const lineVals = await this._prepareLineVals(cleanLotIds, productId);
        const orderId = this.getOrderId();

        if (orderId) {
            await this._writeInputLinesDirectly(orderId, lineVals);
        } else {
            await this._updateInputLinesInUnsavedRecord(lineVals);
        }
    }

    openPopup() {
        if (!this.canEdit()) {
            this._notify("La selección de entradas solo puede modificarse antes de enviar la orden a taller.", "warning");
            return;
        }

        const productId = this.getProductId();

        if (!productId) {
            this._notify("Selecciona un producto de entrada para cargar lotes disponibles.", "warning");
            return;
        }

        this.destroyPopup();

        this._popupRoot = document.createElement("div");
        this._popupRoot.className = "wlp-root";
        document.body.appendChild(this._popupRoot);

        this._renderPopupDOM(productId);
    }

    async _renderPopupDOM(productId) {
        const PAGE_SIZE = 35;
        const root = this._popupRoot;
        const self = this;

        const popupState = {
            quants: [],
            totalCount: 0,
            page: 0,
            hasMore: false,
            isLoading: false,
            isLoadingMore: false,
            pendingIds: new Set(this._getCurrentLotIds()),
            filters: {
                lot_name: "",
                bloque: "",
                atado: "",
                alto_min: "",
                ancho_min: "",
                tipo: "",
            },
            qtyCache: {},
            cachedQuantIds: new Set(),
        };

        let searchTimeout = null;

        root.innerHTML = `
            <div class="wlp-overlay" id="wlp-overlay">
                <div class="wlp-container">
                    <div class="wlp-header">
                        <div class="wlp-title">
                            <i class="fa fa-th-large"></i>
                            <div>
                                <strong>Seleccionar lotes para taller</strong>
                                <span>${this._escapeHtml(this.getProductName())}</span>
                            </div>
                        </div>
                        <div class="wlp-header-actions">
                            <span class="wlp-badge">
                                <i class="fa fa-check-circle"></i>
                                <span id="wlp-count">${popupState.pendingIds.size}</span> seleccionados
                            </span>
                            <span class="wlp-badge wlp-badge-area">
                                <i class="fa fa-balance-scale"></i>
                                <span id="wlp-area">0.00</span> m²
                            </span>
                            <button type="button" class="wlp-btn wlp-btn-primary" id="wlp-confirm-top">
                                <i class="fa fa-check"></i> Confirmar
                            </button>
                            <button type="button" class="wlp-btn wlp-btn-ghost" id="wlp-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="wlp-filters">
                        <label>Lote<input type="text" id="wlf-lot" placeholder="Buscar lote"/></label>
                        <label>Bloque<input type="text" id="wlf-bloque" placeholder="Bloque"/></label>
                        <label>Atado<input type="text" id="wlf-atado" placeholder="Atado"/></label>
                        <label>Alto mín.<input type="number" id="wlf-alto" step="0.01" placeholder="0"/></label>
                        <label>Ancho mín.<input type="number" id="wlf-ancho" step="0.01" placeholder="0"/></label>
                        <label>Tipo
                            <select id="wlf-tipo">
                                <option value="">Todos</option>
                                <option value="placa">Placa</option>
                                <option value="formato">Formato</option>
                                <option value="pieza">Pieza</option>
                                <option value="pallet">Pallet</option>
                            </select>
                        </label>
                        <div class="wlp-filter-actions">
                            <button type="button" class="wlp-btn wlp-btn-soft" id="wlp-select-all">
                                <i class="fa fa-check-square-o"></i> Todo visible
                            </button>
                            <button type="button" class="wlp-btn wlp-btn-danger-soft" id="wlp-clear">
                                <i class="fa fa-square-o"></i> Limpiar
                            </button>
                        </div>
                        <div class="wlp-spacer"></div>
                        <span class="wlp-stat" id="wlp-stat">
                            <i class="fa fa-circle-o-notch fa-spin"></i> Buscando...
                        </span>
                    </div>

                    <div class="wlp-body" id="wlp-body">
                        <div class="wlp-empty">
                            <i class="fa fa-circle-o-notch fa-spin"></i>
                            <span>Cargando inventario...</span>
                        </div>
                    </div>

                    <div class="wlp-footer">
                        <span id="wlp-footer-info">—</span>
                        <div class="wlp-footer-actions">
                            <button type="button" class="wlp-btn wlp-btn-outline" id="wlp-cancel">Cancelar</button>
                            <button type="button" class="wlp-btn wlp-btn-primary" id="wlp-confirm-bottom">
                                <i class="fa fa-check"></i> Agregar selección
                            </button>
                        </div>
                    </div>
                </div>
            </div>`;

        const body = root.querySelector("#wlp-body");
        const stat = root.querySelector("#wlp-stat");
        const footerInfo = root.querySelector("#wlp-footer-info");
        const countEl = root.querySelector("#wlp-count");
        const areaEl = root.querySelector("#wlp-area");

        const cacheQuant = (quant) => {
            if (!quant || !quant.lot_id) return;

            const quantKey = String(quant.id);

            if (popupState.cachedQuantIds.has(quantKey)) return;

            popupState.cachedQuantIds.add(quantKey);

            const lotId = quant.lot_id[0];
            const key = String(lotId);

            if (!popupState.qtyCache[key]) {
                popupState.qtyCache[key] = {
                    qty: 0,
                    tipo: (quant.x_tipo || "placa").toLowerCase(),
                };
            }

            popupState.qtyCache[key].qty += quant.quantity || 0;
        };

        const cacheQuantList = (items) => {
            for (const item of items || []) {
                cacheQuant(item);
            }
        };

        const computeSelectedArea = () => {
            let total = 0;

            for (const lotId of popupState.pendingIds) {
                const cached = popupState.qtyCache[String(lotId)];
                if (cached) {
                    total += cached.qty || 0;
                }
            }

            return total;
        };

        const updateCounters = () => {
            countEl.textContent = popupState.pendingIds.size;
            areaEl.textContent = self.formatNum(computeSelectedArea());
        };

        const ensureQtyCacheForPending = async () => {
            const missingIds = Array.from(popupState.pendingIds).filter((lotId) => {
                return !popupState.qtyCache[String(lotId)];
            });

            if (!missingIds.length) return;

            try {
                const items = await self.orm.call(
                    "stock.quant",
                    "search_workshop_lot_inventory",
                    [],
                    {
                        product_id: productId,
                        filters: {},
                        current_lot_ids: missingIds,
                        location_id: self.getLocationSrcId() || false,
                        order_id: self.getOrderId() || false,
                    }
                );

                cacheQuantList(
                    (items || []).filter((q) => q.lot_id && missingIds.includes(q.lot_id[0]))
                );
            } catch (error) {
                console.warn("[WORKSHOP LOT SELECTOR] No se pudo precargar selección actual:", error);
            }
        };

        const updateStats = () => {
            stat.innerHTML = `${popupState.totalCount} lotes`;
            footerInfo.innerHTML = `<strong>${popupState.quants.length}</strong> de <strong>${popupState.totalCount}</strong> registros visibles`;
        };

        const renderTable = () => {
            updateCounters();
            updateStats();

            if (!popupState.quants.length && !popupState.isLoading) {
                body.innerHTML = `
                    <div class="wlp-empty">
                        <i class="fa fa-inbox"></i>
                        <span>No hay lotes disponibles con estos filtros.</span>
                    </div>`;
                return;
            }

            let rows = "";

            for (const quant of popupState.quants) {
                cacheQuant(quant);

                const lotId = quant.lot_id ? quant.lot_id[0] : 0;
                const lotName = quant.lot_id ? quant.lot_id[1] : "-";
                const selected = popupState.pendingIds.has(lotId);
                const tipo = (quant.x_tipo || "placa").toLowerCase();
                const location = quant.location_id ? String(quant.location_id[1]).split("/").pop() : "-";

                const photo = quant.x_fotografia_principal
                    ? `<img src="data:image/jpeg;base64,${quant.x_fotografia_principal}" alt="Foto"/>`
                    : `<i class="fa fa-picture-o"></i>`;

                const status = selected
                    ? `<span class="wlp-tag wlp-tag-selected">Selec.</span>`
                    : `<span class="wlp-tag wlp-tag-free">Libre</span>`;

                rows += `
                    <tr data-lot-id="${lotId}" class="${selected ? "is-selected" : ""}">
                        <td class="wlp-col-check">
                            <span class="wlp-check">${selected ? '<i class="fa fa-check"></i>' : ""}</span>
                        </td>
                        <td class="wlp-col-photo">
                            <span class="wlp-photo">${photo}</span>
                        </td>
                        <td class="wlp-cell-lot">${self._escapeHtml(lotName)}</td>
                        <td>${self._escapeHtml(quant.x_bloque || "-")}</td>
                        <td>${self._escapeHtml(quant.x_atado || "-")}</td>
                        <td class="text-end">${self.formatDim(quant.x_alto)}</td>
                        <td class="text-end">${self.formatDim(quant.x_ancho)}</td>
                        <td class="text-end">${self.formatDim(quant.x_grosor)}</td>
                        <td class="text-end fw-bold">${self.formatNum(quant.quantity)}</td>
                        <td><span class="wlp-type">${self._escapeHtml(tipo || "-")}</span></td>
                        <td>${self._escapeHtml(quant.x_color || "-")}</td>
                        <td class="text-muted">${self._escapeHtml(location)}</td>
                        <td>${status}</td>
                    </tr>`;
            }

            const sentinel = `
                <div id="wlp-sentinel" class="wlp-sentinel">
                    ${popupState.isLoadingMore ? '<i class="fa fa-circle-o-notch fa-spin"></i> Cargando más...' : ""}
                    ${popupState.hasMore && !popupState.isLoadingMore ? "<span>Más resultados</span>" : ""}
                </div>`;

            body.innerHTML = `
                <table class="wlp-table">
                    <thead>
                        <tr>
                            <th class="wlp-col-check">✓</th>
                            <th class="wlp-col-photo">Foto</th>
                            <th>Lote</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="text-end">Alto</th>
                            <th class="text-end">Largo</th>
                            <th class="text-end">Esp.</th>
                            <th class="text-end">M²</th>
                            <th>Tipo</th>
                            <th>Color</th>
                            <th>Ubic.</th>
                            <th>Estado</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
                ${sentinel}`;

            body.querySelectorAll("tr[data-lot-id]").forEach((tr) => {
                tr.addEventListener("click", () => {
                    const lotId = parseInt(tr.dataset.lotId, 10);

                    if (!lotId) return;

                    if (popupState.pendingIds.has(lotId)) {
                        popupState.pendingIds.delete(lotId);
                    } else {
                        popupState.pendingIds.add(lotId);
                    }

                    renderTable();
                });
            });

            if (self._popupObserver) {
                self._popupObserver.disconnect();
                self._popupObserver = null;
            }

            const sentinelEl = body.querySelector("#wlp-sentinel");

            if (sentinelEl && popupState.hasMore) {
                self._popupObserver = new IntersectionObserver(
                    (entries) => {
                        if (entries[0].isIntersecting && popupState.hasMore && !popupState.isLoadingMore) {
                            loadPage(popupState.page + 1, false);
                        }
                    },
                    { root: body, rootMargin: "140px", threshold: 0.1 }
                );

                self._popupObserver.observe(sentinelEl);
            }
        };

        const loadPage = async (page, reset) => {
            if (reset) {
                popupState.isLoading = true;
                popupState.quants = [];
                popupState.page = 0;
                popupState.qtyCache = {};
                popupState.cachedQuantIds = new Set();

                stat.innerHTML = `<i class="fa fa-circle-o-notch fa-spin"></i> Buscando...`;
                body.innerHTML = `
                    <div class="wlp-empty">
                        <i class="fa fa-circle-o-notch fa-spin"></i>
                        <span>Buscando inventario...</span>
                    </div>`;
            } else {
                popupState.isLoadingMore = true;
            }

            try {
                const result = await self.orm.call(
                    "stock.quant",
                    "search_workshop_lot_inventory_paginated",
                    [],
                    {
                        product_id: productId,
                        filters: popupState.filters,
                        current_lot_ids: Array.from(popupState.pendingIds),
                        page,
                        page_size: PAGE_SIZE,
                        location_id: self.getLocationSrcId() || false,
                        order_id: self.getOrderId() || false,
                    }
                );

                const items = result.items || [];

                cacheQuantList(items);

                popupState.quants = reset || page === 0
                    ? items
                    : [...popupState.quants, ...items];

                popupState.totalCount = result.total || 0;
                popupState.page = page;
                popupState.hasMore = popupState.quants.length < popupState.totalCount;

                await ensureQtyCacheForPending();
            } catch (error) {
                console.error("[WORKSHOP LOT SELECTOR] Error:", error);

                body.innerHTML = `
                    <div class="wlp-empty is-error">
                        <i class="fa fa-exclamation-triangle"></i>
                        <span>${self._escapeHtml(error.message || error.toString())}</span>
                    </div>`;

                return;
            } finally {
                popupState.isLoading = false;
                popupState.isLoadingMore = false;
            }

            renderTable();
        };

        const bindFilter = (id, key) => {
            const input = root.querySelector(`#${id}`);

            if (!input) return;

            const handler = (ev) => {
                popupState.filters[key] = ev.target.value;

                if (searchTimeout) clearTimeout(searchTimeout);

                searchTimeout = setTimeout(() => loadPage(0, true), 350);
            };

            input.addEventListener("input", handler);
            input.addEventListener("change", handler);
        };

        const doConfirm = async () => {
            const selected = Array.from(popupState.pendingIds);

            try {
                await self._rebuildInputLines(selected);
                self.destroyPopup();
            } catch (error) {
                console.error("[WORKSHOP LOT SELECTOR] Confirm error:", error);
                self._notify(error.message || "No se pudo actualizar la selección de lotes.", "danger");
            }
        };

        const doClose = () => this.destroyPopup();

        root.querySelector("#wlp-close").addEventListener("click", doClose);
        root.querySelector("#wlp-cancel").addEventListener("click", doClose);
        root.querySelector("#wlp-confirm-top").addEventListener("click", doConfirm);
        root.querySelector("#wlp-confirm-bottom").addEventListener("click", doConfirm);

        root.querySelector("#wlp-select-all").addEventListener("click", () => {
            for (const quant of popupState.quants) {
                if (quant.lot_id && quant.lot_id[0]) {
                    popupState.pendingIds.add(quant.lot_id[0]);
                }
            }

            renderTable();
        });

        root.querySelector("#wlp-clear").addEventListener("click", () => {
            popupState.pendingIds = new Set();
            renderTable();
        });

        root.querySelector("#wlp-overlay").addEventListener("click", (ev) => {
            if (ev.target.id === "wlp-overlay") doClose();
        });

        const keyHandler = (ev) => {
            if (ev.key === "Escape") doClose();
        };

        document.addEventListener("keydown", keyHandler);
        this._popupKeyHandler = keyHandler;

        bindFilter("wlf-lot", "lot_name");
        bindFilter("wlf-bloque", "bloque");
        bindFilter("wlf-atado", "atado");
        bindFilter("wlf-alto", "alto_min");
        bindFilter("wlf-ancho", "ancho_min");
        bindFilter("wlf-tipo", "tipo");

        await loadPage(0, true);
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
}

registry.category("fields").add("workshop_lot_selector", {
    component: WorkshopLotSelector,
    displayName: "Selector visual de lotes de taller",
});