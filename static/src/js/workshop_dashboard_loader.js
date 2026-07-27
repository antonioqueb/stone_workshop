/** @odoo-module **/

// Cargador perezoso del Panel de Taller (~58 KB de js/xml/css que antes
// viajaban en cada arranque del webclient). El componente real vive en el
// bundle 'stone_workshop.assets_dashboard' y se carga al abrir la acción.

import { Component, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { LazyComponent } from "@web/core/assets";

class LazyWorkshopDashboard extends Component {
    static components = { LazyComponent };
    static template = xml`
        <LazyComponent bundle="'stone_workshop.assets_dashboard'" Component="'StoneWorkshopDashboard'" props="props"/>
    `;
    static props = { "*": true };
}

registry.category("actions").add("stone_workshop_dashboard", LazyWorkshopDashboard);
