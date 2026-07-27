/** @odoo-module **/
/**
 * Selector visual de lotes para la Reclasificación de Lotes.
 *
 * Clon adaptado del WorkshopLotSelector (mismo patrón, mismos estilos
 * wls-/wlp- y mismo backend stock.quant.search_workshop_lot_inventory_
 * paginated), pero escribiendo sobre stock.lot.reclassification.line:
 * cada lote seleccionado se convierte en una línea {lot_from_id} y la
 * existencia (qty_available) la calcula SIEMPRE el servidor.
 */
import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

export class ReclassificationLotSelector extends Component {
    static template = "stone_workshop.ReclassificationLotSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this.state = useState({
            version: 0,
        });

        // Metadata de lotes (bloque, atado, dimensiones) para pintar el panel;
        // se llena desde el servidor y desde el popup. lotId -> info.
        this._lotInfoCache = new Map();

        this._popupRoot = null;
        this._popupKeyHandler = null;
        this._popupObserver = null;

        onWillStart(async () => {
            await this._ensureLotMetadata(this._recordLotIds());
        });

        onWillUpdateProps(async () => {
            // Metadata de lotes que aún no esté en cache (sin bloquear el render).
            this._ensureLotMetadata(this._recordLotIds()).then(() => {
                this.state.version += 1;
            });
            this.state.version += 1;
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    // ------------------------------------------------------------------
    // Utilerías (idénticas al patrón de los demás selectores)
    // ------------------------------------------------------------------

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

    formatNum(value) {
        const num = parseFloat(value || 0);
        return Number.isFinite(num) ? num.toFixed(2) : "0.00";
    }

    formatDim(value) {
        const num = parseFloat(value || 0);
        if (!Number.isFinite(num) || !num) return "-";
        return num % 1 === 0 ? num.toFixed(0) : num.toFixed(2);
    }

    // ------------------------------------------------------------------
    // Lectura del registro padre
    // ------------------------------------------------------------------

    getRecId(props = this.props) {
        const id = props.record.resId || props.record.data.id || false;
        return typeof id === "number" && id > 0 ? id : false;
    }

    _getRecState(props = this.props) {
        const state = props.record.data.state;
        if (!state) return "draft";
        if (typeof state === "string") return state;
        if (typeof state === "object") return state.value || state.raw_value || state.name || "draft";
        return String(state || "draft");
    }

    isDone() {
        return this._getRecState() === "done";
    }

    canEdit() {
        return !this.props.readonly && this._getRecState() === "draft";
    }

    getProductId() {
        return this._extractId(this.props.record.data.product_from_id);
    }

    getProductName() {
        return this._extractName(this.props.record.data.product_from_id);
    }

    _getX2ManyRecords() {
        const value = this.props.record.data.line_ids;
        if (!value) return [];
        if (Array.isArray(value.records)) return value.records;
        if (Array.isArray(value)) return value;
        return [];
    }

    // ------------------------------------------------------------------
    // Filas seleccionadas (servidor si el registro existe, memoria si no)
    // ------------------------------------------------------------------

    _cacheLotInfo(lotId, info) {
        if (!lotId) return;
        const prev = this._lotInfoCache.get(lotId) || {};
        this._lotInfoCache.set(lotId, { ...prev, ...info });
    }

    _lotInfo(lotId) {
        return this._lotInfoCache.get(lotId) || {};
    }

    _recordLotIds() {
        return this._getX2ManyRecords()
            .map((record) => this._extractId((record.data || record).lot_from_id))
            .filter(Boolean);
    }

    async _ensureLotMetadata(lotIds) {
        const missing = Array.from(new Set(lotIds || [])).filter(
            (id) => id && !this._lotInfoCache.has(id)
        );
        if (!missing.length) return;

        try {
            const lots = await this.orm.searchRead(
                "stock.lot",
                [["id", "in", missing]],
                ["id", "x_bloque", "x_atado", "x_alto", "x_ancho", "x_grosor", "x_tipo"]
            );
            for (const lot of lots || []) {
                this._cacheLotInfo(lot.id, {
                    bloque: lot.x_bloque || "",
                    atado: lot.x_atado || "",
                    alto: lot.x_alto || 0,
                    ancho: lot.x_ancho || 0,
                    grosor: lot.x_grosor || 0,
                    tipo: lot.x_tipo || "",
                });
            }
        } catch (error) {
            console.warn("[RECLA SELECTOR] Sin metadata de lotes:", error);
        }
    }

    get selectedRows() {
        void this.state.version;

        const rows = this._getX2ManyRecords()
            .map((record, index) => {
                const data = record.data || record;
                const lotId = this._extractId(data.lot_from_id);
                return {
                    key: record.id || record.resId || lotId || index,
                    lot_id: lotId,
                    lot_name: this._extractName(data.lot_from_id) || "-",
                    qty: this._extractNumber(data.qty_available),
                    lot_to_name: this._extractName(data.lot_to_id) || "",
                    qty_moved: this._extractNumber(data.qty_moved),
                    location_note: data.location_note || "",
                };
            })
            .filter((row) => row.lot_id);

        return rows.map((row) => {
            const info = this._lotInfo(row.lot_id);
            const qty = row.qty || info.qty || 0;
            return { ...row, ...{
                bloque: info.bloque || "",
                atado: info.atado || "",
                alto: info.alto || 0,
                ancho: info.ancho || 0,
                grosor: info.grosor || 0,
                tipo: info.tipo || "",
                qty,
            } };
        });
    }

    get totalQty() {
        return this.selectedRows.reduce((total, row) => {
            return total + (this.isDone() ? row.qty_moved || 0 : row.qty || 0);
        }, 0);
    }

    _getCurrentLotIds() {
        return this.selectedRows.map((row) => row.lot_id).filter(Boolean);
    }

    // ------------------------------------------------------------------
    // Escritura de líneas
    // ------------------------------------------------------------------

    async removeLot(lotId, ev = null) {
        if (ev) ev.stopPropagation();
        if (!this.canEdit()) {
            this._notify("Solo puedes modificar la selección en borrador.", "warning");
            return;
        }
        const nextLotIds = this._getCurrentLotIds().filter((id) => id !== lotId);
        await this._rebuildLines(nextLotIds);
    }

    async _rebuildLines(lotIds) {
        if (!this.canEdit()) {
            this._notify("Solo puedes modificar la selección en borrador.", "warning");
            return;
        }

        const cleanLotIds = Array.from(
            new Set((lotIds || []).map((id) => parseInt(id, 10)).filter(Boolean))
        );

        // El m2o necesita [id, nombre] para pintarse en el modelo del form.
        let nameMap = new Map();
        if (cleanLotIds.length) {
            try {
                const lots = await this.orm.read("stock.lot", cleanLotIds, ["display_name"]);
                nameMap = new Map((lots || []).map((l) => [l.id, l.display_name || String(l.id)]));
            } catch (error) {
                console.warn("[RECLA SELECTOR] Sin display_name de lotes:", error);
            }
        }

        // record.update dispara el onchange del formulario: qty_available de
        // cada línea y total_qty del encabezado se recalculan EN VIVO, y todo
        // se persiste junto al guardar (o al Aplicar, que guarda primero).
        await this.props.record.update({
            line_ids: [
                [5, 0, 0],
                ...cleanLotIds.map((lotId) => [
                    0, 0,
                    { lot_from_id: [lotId, nameMap.get(lotId) || String(lotId)] },
                ]),
            ],
        });

        await this._ensureLotMetadata(cleanLotIds);
        this.state.version += 1;
    }

    // ------------------------------------------------------------------
    // Popup (mismo patrón/estilos wlp- del selector de taller)
    // ------------------------------------------------------------------

    openPopup() {
        if (!this.canEdit()) {
            this._notify("Solo puedes modificar la selección en borrador.", "warning");
            return;
        }

        const productId = this.getProductId();
        if (!productId) {
            this._notify("Selecciona primero el producto origen.", "warning");
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
                            <i class="fa fa-exchange"></i>
                            <div>
                                <strong>Seleccionar lotes a reclasificar</strong>
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
            const lotId = quant.lot_id[0];

            // Metadata para el panel del formulario.
            self._cacheLotInfo(lotId, {
                bloque: quant.x_bloque || "",
                atado: quant.x_atado || "",
                alto: quant.x_alto || 0,
                ancho: quant.x_ancho || 0,
                grosor: quant.x_grosor || 0,
                tipo: (quant.x_tipo || "").toLowerCase(),
            });

            if (popupState.cachedQuantIds.has(quantKey)) return;
            popupState.cachedQuantIds.add(quantKey);

            const key = String(lotId);
            if (!popupState.qtyCache[key]) {
                popupState.qtyCache[key] = { qty: 0 };
            }
            popupState.qtyCache[key].qty += quant.quantity || 0;

            const cachedInfo = self._lotInfoCache.get(lotId) || {};
            self._cacheLotInfo(lotId, { qty: (cachedInfo.qty || 0) + (quant.quantity || 0) });
        };

        const cacheQuantList = (items) => {
            for (const item of items || []) {
                cacheQuant(item);
            }
        };

        const computeSelectedQty = () => {
            let total = 0;
            for (const lotId of popupState.pendingIds) {
                const cached = popupState.qtyCache[String(lotId)];
                if (cached) total += cached.qty || 0;
            }
            return total;
        };

        const updateCounters = () => {
            countEl.textContent = popupState.pendingIds.size;
            areaEl.textContent = self.formatNum(computeSelectedQty());
        };

        const ensureQtyCacheForPending = async () => {
            const missingIds = Array.from(popupState.pendingIds).filter(
                (lotId) => !popupState.qtyCache[String(lotId)]
            );
            if (!missingIds.length) return;

            try {
                const items = await self.orm.call(
                    "stock.quant",
                    "search_reclassification_lot_inventory",
                    [],
                    {
                        product_id: productId,
                        filters: {},
                        current_lot_ids: missingIds,
                    }
                );
                cacheQuantList(
                    (items || []).filter((q) => q.lot_id && missingIds.includes(q.lot_id[0]))
                );
            } catch (error) {
                console.warn("[RECLA SELECTOR] No se pudo precargar selección actual:", error);
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
                    "search_reclassification_lot_inventory_paginated",
                    [],
                    {
                        product_id: productId,
                        filters: popupState.filters,
                        current_lot_ids: Array.from(popupState.pendingIds),
                        page,
                        page_size: PAGE_SIZE,
                    }
                );

                const items = result.items || [];
                cacheQuantList(items);

                popupState.quants = reset || page === 0 ? items : [...popupState.quants, ...items];
                popupState.totalCount = result.total || 0;
                popupState.page = page;
                popupState.hasMore = popupState.quants.length < popupState.totalCount;

                await ensureQtyCacheForPending();
            } catch (error) {
                console.error("[RECLA SELECTOR] Error:", error);
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
                await self._rebuildLines(selected);
                self.destroyPopup();
            } catch (error) {
                console.error("[RECLA SELECTOR] Confirm error:", error);
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

registry.category("fields").add("reclassification_lot_selector", {
    component: ReclassificationLotSelector,
    displayName: "Selector visual de lotes a reclasificar",
    supportedTypes: ["boolean"],
});
