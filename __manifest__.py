{
    'name': 'Stone Workshop',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing',
    'summary': 'Taller de piedra - Procesos de acabado y corte de placas',
    'description': 'Módulo especializado para talleres de piedra natural. '
                   'Gestión de procesos de acabado y corte con interfaz visual simplificada.',
    'author': 'Alphaqueb Consulting',
    'website': 'https://www.alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'mrp',
        'stock',
        'product',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/workshop_order_views.xml',
        'views/workshop_process_views.xml',
        'views/workshop_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'stone_workshop/static/src/css/workshop.css',
            'stone_workshop/static/src/js/workshop_kanban.js',
            'stone_workshop/static/src/xml/workshop_templates.xml',
        ],
    },
    'installable': True,
    'application': False,
}
