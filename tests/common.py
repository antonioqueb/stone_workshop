# -*- coding: utf-8 -*-
"""Datos base para las pruebas del taller: producto en m² con lotes (placas),
existencias en el almacén de la compañía y procesos de acabado y corte."""
from odoo.tests.common import TransactionCase


class WorkshopCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, mail_notrack=True,
            mail_create_nolog=True, mail_create_nosubscribe=True))
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.stock_loc = cls.warehouse.lot_stock_id
        cls.uom_m2 = cls._find_m2()

        Product = cls.env['product.product']
        base = {'is_storable': True, 'tracking': 'lot', 'uom_id': cls.uom_m2.id}
        if 'uom_po_id' in Product._fields:
            base['uom_po_id'] = cls.uom_m2.id
        cls.slab = Product.create(dict(base, name='PRUEBA TALLER placa base'))
        cls.finished = Product.create(dict(base, name='PRUEBA TALLER placa pulida'))
        cls.cut = Product.create(dict(base, name='PRUEBA TALLER formato cortado'))

        Process = cls.env['workshop.process']
        cls.p_finish = Process.create({'name': 'PRUEBA Pulido', 'process_type': 'finish'})
        cls.p_cut = Process.create({'name': 'PRUEBA Corte', 'process_type': 'cut'})

    @classmethod
    def _find_m2(cls):
        UoM = cls.env['uom.uom']
        for xmlid in ('uom.uom_square_meter', 'uom.product_uom_square_meter'):
            rec = cls.env.ref(xmlid, raise_if_not_found=False)
            if rec:
                return rec
        return UoM.search(['|', ('name', 'ilike', 'm²'), ('name', 'ilike', 'm2')], limit=1)

    # ---------------------------------------------------------------- helpers
    def make_lot(self, name, qty, product=None):
        product = product or self.slab
        lot = self.env['stock.lot'].create({
            'name': name, 'product_id': product.id, 'company_id': self.company.id})
        self.env['stock.quant']._update_available_quantity(
            product, self.stock_loc, qty, lot_id=lot)
        return lot

    def make_order(self, process, lots_qty, out_product=None):
        """lots_qty: [(lot, qty_m2)]"""
        return self.env['workshop.order'].create({
            'process_id': process.id,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
            'input_product_id': self.slab.id,
            'default_product_out_id': (out_product or (
                self.finished if process == self.p_finish else self.cut)).id,
            'input_line_ids': [(0, 0, {
                'product_id': self.slab.id, 'lot_id': lot.id,
                'qty_in': qty, 'area_sqm': qty, 'location_id': self.stock_loc.id,
            }) for lot, qty in lots_qty],
        })

    def log(self, order, consumptions, produced, pieces=0):
        """consumptions: [(input_line, m2)]"""
        vals = {
            'order_id': order.id,
            'area_sqm': produced,
            'consumption_line_ids': [(0, 0, {
                'input_line_id': line.id, 'consumed_sqm': m2}) for line, m2 in consumptions],
        }
        if pieces:
            vals['pieces_out'] = pieces
        return self.env['workshop.progress.log'].create(vals)

    def qty_at(self, lot, location):
        return sum(self.env['stock.quant'].search([
            ('lot_id', '=', lot.id), ('location_id', 'child_of', location.id)]).mapped('quantity'))
