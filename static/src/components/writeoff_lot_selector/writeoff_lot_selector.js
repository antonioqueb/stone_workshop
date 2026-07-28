/** @odoo-module **/
/**
 * Selector visual de lotes para la Baja de Material (write-off).
 *
 * Subclase de ReclassificationLotSelector: mismo popup, mismos estilos
 * wls-/wlp- y misma búsqueda backend (lotes libres del producto, sin
 * reservas, holds ni compromisos). Solo cambian template y textos.
 * El modelo stock.lot.writeoff usa los mismos nombres de campo
 * (product_from_id, line_ids.lot_from_id...) precisamente para heredar
 * este componente sin duplicar lógica.
 */
import { registry } from "@web/core/registry";
import { ReclassificationLotSelector } from "@stone_workshop/components/reclassification_lot_selector/reclassification_lot_selector";

export class WriteoffLotSelector extends ReclassificationLotSelector {
    static template = "stone_workshop.WriteoffLotSelector";

    get popupTitle() {
        return "Seleccionar lotes a dar de baja";
    }

    get popupIcon() {
        return "fa-trash";
    }

    get missingProductMsg() {
        return "Selecciona primero el producto.";
    }
}

registry.category("fields").add("writeoff_lot_selector", {
    component: WriteoffLotSelector,
    displayName: "Selector visual de lotes a dar de baja",
    supportedTypes: ["one2many"],
});
