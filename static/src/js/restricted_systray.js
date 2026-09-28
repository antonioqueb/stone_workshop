/** @odoo-module **/
/**
 * Usuario "Solo Taller e Inventario": fuera el chat y las llamadas de la
 * barra superior (Conversaciones abre mensajes de todo el sistema). Los
 * menús los filtra el servidor (models/ir_ui_menu.py). El reloj de
 * actividades ya lo quita theme_list_modern para todos.
 *
 * Es un servicio porque el webclient espera a que arranquen todos antes de
 * pintar la barra: así el icono nunca llega a aparecer.
 */
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

const HIDDEN_SYSTRAY_ITEMS = ["mail.messaging_menu", "discuss.CallMenu"];

registry.category("services").add("stone_workshop_restricted_systray", {
    async start() {
        let restricted = false;
        try {
            restricted = await user.hasGroup("stone_workshop.group_workshop_restricted");
        } catch {
            return;
        }
        if (!restricted) {
            return;
        }
        const systray = registry.category("systray");
        for (const key of HIDDEN_SYSTRAY_ITEMS) {
            if (systray.contains(key)) {
                systray.remove(key);
            }
        }
    },
});
