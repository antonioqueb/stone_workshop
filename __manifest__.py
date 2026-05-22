{
    'name': 'Stone Workshop',
    'version': '19.0.2.3.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra con transformación real de placas, formatos, retazos y merma',
    'description': '''
Stone Workshop rediseñado para negocio de piedra natural.

Soporta:
- Acabado masivo de placas.
- Corte de placas en múltiples salidas.
- Procesamiento agregado de formatos / pallets.
- Reproceso o reparación.
- Trazabilidad lote origen / lote resultado.
- Control de merma, retazos y disponibilidad.
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
