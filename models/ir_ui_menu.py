# -*- coding: utf-8 -*-
"""Usuario encerrado en Taller + Inventario.

En Odoo los grupos de un menú solo SUMAN visibilidad: no hay forma de
quitarle un menú a alguien por tener un grupo. Muchas apps (Buscador
Visual, Galería, Tableros, Aplicaciones, Actividades…) no llevan grupo o se
abren con el de usuario interno, que todo usuario necesita. Para el
operador de taller (usuario TALLER, 28 sep 2026) se invierte la lógica: con
`group_workshop_restricted` solo quedan visibles los menús que cuelgan de
Inventario y de Taller de Piedra — cualquier app instalada después queda
oculta sola.
"""
from odoo import api, models, tools

RESTRICTED_GROUP = 'stone_workshop.group_workshop_restricted'
ALLOWED_ROOT_XMLIDS = (
    'stock.menu_stock_root',
    'stone_workshop.menu_workshop_root',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    @tools.ormcache('frozenset(self.env.user._get_group_ids())', 'debug')
    def _visible_menu_ids(self, debug=False):
        visible = super()._visible_menu_ids(debug=debug)
        IMD = self.env['ir.model.data']
        group_id = IMD._xmlid_to_res_id(RESTRICTED_GROUP, raise_if_not_found=False)
        if not group_id or group_id not in self.env.user._get_group_ids():
            return visible
        root_ids = [
            IMD._xmlid_to_res_id(xmlid, raise_if_not_found=False)
            for xmlid in ALLOWED_ROOT_XMLIDS
        ]
        prefixes = tuple('%s/' % root_id for root_id in root_ids if root_id)
        if not prefixes:
            return visible
        menus = self.sudo().browse(visible)
        return frozenset(
            menu.id for menu in menus
            if (menu.parent_path or '').startswith(prefixes)
        )
