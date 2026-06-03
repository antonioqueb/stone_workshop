{
    'name': 'Stone Workshop',
    'version': '19.0.9.1.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra en 3 pasos; panel con cola priorizada y bitácora declarativa',
    'description': '''
Stone Workshop rediseñado para negocio de piedra natural.

Flujo simplificado a tres pasos: borrador, confirmar taller (consume material y
pre-llena salidas sugeridas) y declarar resultado (cuadra la merma residual,
materializa producción y cierra la orden).

Durante el paso "en taller" el usuario puede:
- Registrar la bitácora diaria de avance (fecha, actividad, cantidad, área, notas)
  para órdenes que tomen varios días.
- Marcar como no usadas las placas que no se procesaron; al declarar el resultado
  se devuelven íntegras al stock de origen.

Soporta:
- Acabado masivo de placas.
- Corte de placas en múltiples salidas.
- Procesamiento agregado de formatos / pallets.
- Reproceso o reparación.
- Trazabilidad lote origen / lote resultado.
- Cuadre automático de merma como residual.
''',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'product',
        'mail',
        'web',
    ],
    'data': [
        'security/workshop_security.xml',
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/workshop_process_views.xml',
        'views/workshop_order_views.xml',
        'views/workshop_menus.xml',
        'reports/workshop_pick_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stone_workshop/static/src/css/workshop.css',
            'stone_workshop/static/src/scss/workshop_lot_selector.scss',
            'stone_workshop/static/src/js/workshop_dashboard.js',
            'stone_workshop/static/src/components/workshop_lot_selector/workshop_lot_selector.xml',
            'stone_workshop/static/src/components/workshop_lot_selector/workshop_lot_selector.js',
            'stone_workshop/static/src/xml/workshop_templates.xml',
        ],
    },
    'installable': True,
    'application': False,
}
