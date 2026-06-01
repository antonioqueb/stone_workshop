{
    'name': 'Stone Workshop',
    'version': '19.0.3.0.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra declarativo: captura el resultado real y la merma se calcula sola',
    'description': '''
Stone Workshop rediseñado para negocio de piedra natural.

Modo declarativo: el usuario captura las salidas reales (útil y retazos) al final
de la operación; la merma se calcula automáticamente como el residual del balance
de m². La planeación (target, yield, loss%) queda como opcional/informativa.

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
