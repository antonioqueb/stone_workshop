# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class WorkshopProcessRecipe(models.Model):
    """Receta de proceso: producto origen + proceso de taller → producto final.

    Catálogo de configuración (Inventario / Taller): permite que el selector
    de cadena multi-proceso deduzca solo qué producto entrega cada paso, sin
    preguntárselo al usuario. Si no hay receta, el paso se captura manual.
    """
    _name = 'workshop.process.recipe'
    _description = 'Receta de proceso de taller'
    _order = 'input_product_id, process_id, id'

    active = fields.Boolean(default=True)
    input_product_id = fields.Many2one(
        'product.product',
        string='Producto origen',
        required=True,
        index=True,
        domain=[('tracking', '!=', 'none')],
        help='Producto que entra al proceso (lo que el paso recibe).',
    )
    process_id = fields.Many2one(
        'workshop.process',
        string='Proceso de taller',
        required=True,
        index=True,
    )
    output_product_id = fields.Many2one(
        'product.product',
        string='Producto final',
        required=True,
        domain=[('tracking', '!=', 'none')],
        help='Producto que produce el proceso (lo que el paso entrega).',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        help='Vacío = aplica para todas las compañías.',
    )
    name = fields.Char(compute='_compute_name', store=True)

    @api.depends('input_product_id', 'process_id', 'output_product_id')
    def _compute_name(self):
        for recipe in self:
            recipe.name = '%s + %s → %s' % (
                recipe.input_product_id.display_name or '?',
                recipe.process_id.name or '?',
                recipe.output_product_id.display_name or '?',
            )

    @api.constrains('input_product_id', 'process_id', 'company_id', 'active')
    def _check_unique_recipe(self):
        for recipe in self:
            if not recipe.active:
                continue
            domain = [
                ('id', '!=', recipe.id),
                ('input_product_id', '=', recipe.input_product_id.id),
                ('process_id', '=', recipe.process_id.id),
            ]
            if recipe.company_id:
                domain += [('company_id', 'in', [recipe.company_id.id, False])]
            duplicate = self.search(domain, limit=1)
            if duplicate:
                raise ValidationError(_(
                    'Ya existe una receta para %(product)s con el proceso '
                    '%(process)s (%(name)s). Un mismo origen + proceso solo '
                    'puede producir un producto final.'
                ) % {
                    'product': recipe.input_product_id.display_name,
                    'process': recipe.process_id.name,
                    'name': duplicate.name,
                })

    @api.constrains('input_product_id', 'output_product_id')
    def _check_products_differ(self):
        for recipe in self:
            if recipe.input_product_id == recipe.output_product_id:
                raise ValidationError(_(
                    'El producto origen y el producto final deben ser '
                    'distintos (el proceso transforma el material).'
                ))

    @api.model
    def resolve_output(self, input_product_id, process_id):
        """Producto final para (origen, proceso), o recordset vacío.

        Prefiere la receta de la compañía activa sobre la genérica."""
        if not input_product_id or not process_id:
            return self.env['product.product']
        recipes = self.search([
            ('input_product_id', '=', int(input_product_id)),
            ('process_id', '=', int(process_id)),
            ('company_id', 'in', [self.env.company.id, False]),
        ])
        if not recipes:
            return self.env['product.product']
        specific = recipes.filtered(lambda r: r.company_id)
        return (specific[:1] or recipes[:1]).output_product_id
