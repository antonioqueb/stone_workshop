# -*- coding: utf-8 -*-
"""API para la TABLETA de taller (app móvil sto_scanner).

Los operadores de maquinaria con el grupo `group_workshop_tablet` entran a la
app directo a un panel táctil que replica el panel web del taller: cola
priorizada, órdenes en ejecución con cronómetro, bitácora y declaración del
resultado. Esta capa sólo ORQUESTA lo que ya existe en `workshop.order`
(confirmar, pausar, reanudar, reordenar, declarar) y entrega payloads planos
listos para pintar; no agrega reglas de negocio nuevas.

Todo devuelve diccionarios JSON-serializables y los errores de negocio salen
como UserError/ValidationError para que la app los muestre tal cual.
"""
import html
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .workshop_order import WORKSHOP_PAUSE_REASONS


def _dt(value):
    return fields.Datetime.to_string(value) if value else False


def _d(value):
    return fields.Date.to_string(value) if value else False


def _html_to_text(value):
    if not value:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', str(value))
    text = re.sub(r'</p>\s*<p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return html.unescape(text).strip()


class WorkshopOrderTablet(models.Model):
    _inherit = 'workshop.order'

    # ─── Lectura ────────────────────────────────────────────────────────────
    @api.model
    def get_tablet_access(self):
        """Quién soy y qué puedo hacer desde la tableta."""
        user = self.env.user
        is_tablet = user.has_group('stone_workshop.group_workshop_tablet')
        can_manage = (
            user.has_group('stone_workshop.group_workshop_user')
            or user.has_group('base.group_system')
        )
        return {
            'uid': user.id,
            'name': user.name,
            'is_tablet_operator': bool(is_tablet),
            'can_operate': bool(can_manage),
            'can_reorder': bool(can_manage),
            'is_supervisor': bool(
                user.has_group('stone_workshop.group_workshop_supervisor')
            ),
            'pause_reasons': [
                {'code': code, 'label': label} for code, label in WORKSHOP_PAUSE_REASONS
            ],
        }

    @api.model
    def get_tablet_board(self):
        """Una sola llamada para pintar toda la pantalla principal de la tableta.

        Reúne cola + ejecución (con la regla de 24 h ya aplicada), KPIs del día,
        capacidad y la hora del servidor (para que el cronómetro en vivo no
        dependa del reloj de la tableta).
        """
        board = self.get_workshop_board()
        try:
            kpis = self.get_workshop_kpis()
        except Exception:  # noqa: BLE001 - los KPIs nunca tumban el panel
            kpis = {}
        try:
            capacity = self.get_workshop_capacity_overview()
        except Exception:  # noqa: BLE001
            capacity = {}
        return {
            'server_now': _dt(fields.Datetime.now()),
            'queue': board.get('queue', []),
            'execution': board.get('execution', []),
            'kpis': kpis,
            'capacity': capacity,
            'access': self.get_tablet_access(),
        }

    def _tablet_input_line_payload(self, line):
        self.ensure_one()
        area = self._input_line_area(line)
        consumed = sum(
            (c.consumed_sqm or 0.0)
            for c in line.consumption_line_ids
            if (c.consumed_sqm or 0.0) > 0.0
        )
        lot = line.lot_id
        return {
            'id': line.id,
            'lot_id': lot.id if lot else 0,
            'lot_name': lot.name if lot else '',
            'product': line.product_id.display_name if line.product_id else '',
            'material_type': line.material_type or '',
            'qty': line.qty_in or 0.0,
            'pieces': line.pieces or 0,
            'area_sqm': area or 0.0,
            'consumed_sqm': consumed,
            'remaining_sqm': max(0.0, (area or 0.0) - consumed),
            'width_cm': line.width_cm or 0.0,
            'height_cm': line.height_cm or 0.0,
            'thickness_cm': line.thickness_cm or 0.0,
            'block': line.block_name or '',
            'tone': line.tone or '',
            'finish': line.current_finish or '',
            'location': line.location_id.display_name if line.location_id else '',
            'state': line.state or '',
            'is_consumed': bool(line.is_consumed),
            'is_used': bool(line.is_used),
        }

    @staticmethod
    def _tablet_output_line_payload(line):
        return {
            'id': line.id,
            'output_type': line.output_type or '',
            'product': line.product_id.display_name if line.product_id else '',
            'lot_name': line.lot_name or (line.lot_id.name if line.lot_id else ''),
            'qty': line.qty_out or 0.0,
            'pieces': line.pieces or 0,
            'area_sqm': line.area_sqm or 0.0,
            'width_cm': line.width_cm or 0.0,
            'height_cm': line.height_cm or 0.0,
            'thickness_cm': line.thickness_cm or 0.0,
            'finish': line.finish_result or '',
            'state': line.state or '',
        }

    @staticmethod
    def _tablet_progress_log_payload(log):
        return {
            'id': log.id,
            'date': _d(log.date),
            'responsible': log.responsible_id.name if log.responsible_id else '',
            'area_sqm': log.area_sqm or 0.0,
            'notes': log.notes or '',
            'consumptions': [
                {
                    'input_line_id': c.input_line_id.id,
                    'lot_name': c.input_line_id.lot_id.name if c.input_line_id.lot_id else '',
                    'consumed_sqm': c.consumed_sqm or 0.0,
                }
                for c in log.consumption_line_ids
            ],
        }

    @staticmethod
    def _tablet_session_payload(session):
        reasons = dict(WORKSHOP_PAUSE_REASONS)
        return {
            'id': session.id,
            'start': _dt(session.start),
            'end': _dt(session.end),
            'duration_seconds': session.duration_seconds or 0.0,
            'is_running': bool(session.is_running),
            'responsible': session.responsible_id.name if session.responsible_id else '',
            'pause_reason': session.pause_reason or '',
            'pause_reason_label': reasons.get(session.pause_reason, '') if session.pause_reason else '',
            'pause_note': session.pause_note or '',
        }

    def get_tablet_order_detail(self):
        """Todo lo que el panel lateral de la tableta necesita de UNA orden."""
        self.ensure_one()
        data = self._workshop_board_payload()
        active_inputs = self.input_line_ids.filtered(lambda l: l.state != 'cancelled')
        active_outputs = self.output_line_ids.filtered(lambda l: l.state != 'cancelled')
        logs = self.progress_log_ids.sorted(lambda l: (l.date, l.id), reverse=True)
        sessions = self.work_session_ids.sorted(lambda s: (s.start, s.id), reverse=True)

        unused = active_inputs.filtered(lambda l: l.is_consumed and not l.is_used)
        data.update({
            'server_now': _dt(fields.Datetime.now()),
            'priority': self.priority or '0',
            'process_type': self.process_type or '',
            'notes': _html_to_text(self.notes),
            'date_start': _dt(self.date_start),
            'date_done': _dt(self.date_done),
            'date_planned': _dt(self.date_planned),
            'location_src': self.location_src_id.display_name if self.location_src_id else '',
            'location_workshop': self.location_workshop_id.display_name if self.location_workshop_id else '',
            'location_dest': self.location_dest_id.display_name if self.location_dest_id else '',
            'target_pieces': self.target_pieces or 0,
            'expected_yield_percent': self.expected_yield_percent or 0.0,
            'planned_loss_percent': self.planned_loss_percent or 0.0,
            'area_remnant_total': self.area_remnant_total or 0.0,
            'area_loss_total': self.area_loss_total or 0.0,
            'yield_percent': self.yield_percent or 0.0,
            'loss_percent': self.loss_percent or 0.0,
            'worked_seconds': self.worked_seconds or 0.0,
            'inputs': [self._tablet_input_line_payload(l) for l in active_inputs],
            'outputs': [self._tablet_output_line_payload(l) for l in active_outputs],
            'progress_logs': [self._tablet_progress_log_payload(l) for l in logs],
            'sessions': [self._tablet_session_payload(s) for s in sessions],
            'logged_area_total': sum((l.area_sqm or 0.0) for l in logs),
            'unused_count': len(unused),
            'can_start': self.state == 'draft',
            'can_pause': self.state == 'in_workshop' and bool(self.timer_running),
            'can_resume': self.state == 'in_workshop' and not self.timer_running,
            'can_log_progress': self.state == 'in_workshop',
            'can_declare': self.state == 'in_workshop' and bool(logs),
            'is_mine': self.responsible_id.id == self.env.user.id,
        })
        return data

    # ─── Acciones ───────────────────────────────────────────────────────────
    def tablet_start(self):
        """Mover de la cola a ejecución.

        Borrador → confirma al taller (consume material y arranca el reloj).
        Estacionada por la regla de 24 h → la retoma (sale de la cola).
        """
        self.ensure_one()
        if self.state == 'draft':
            self.action_confirm_workshop()
        elif self.state == 'in_workshop':
            if not self.timer_running:
                self.action_resume_timer()
        else:
            raise UserError(_('La orden %s ya no se puede iniciar (%s).') % (
                self.name, dict(self._fields['state'].selection).get(self.state, self.state)
            ))
        return self.get_tablet_order_detail()

    def tablet_pause(self, reason=False, note=False):
        self.ensure_one()
        valid = {code for code, _label in WORKSHOP_PAUSE_REASONS}
        if reason and reason not in valid:
            reason = 'other'
        self.action_pause_timer(reason=reason or False, note=note or False)
        return self.get_tablet_order_detail()

    def tablet_resume(self):
        self.ensure_one()
        self.action_resume_timer()
        return self.get_tablet_order_detail()

    def tablet_take(self):
        """El operador se asigna como responsable de la orden."""
        self.ensure_one()
        if self.state in ('done', 'cancel'):
            raise UserError(_('La orden %s ya está cerrada.') % self.name)
        self.write({'responsible_id': self.env.user.id})
        self.message_post(body=_('Orden tomada desde tableta por %s.') % self.env.user.name)
        return self.get_tablet_order_detail()

    def tablet_add_progress_log(self, area_sqm, consumptions, notes=False, date=False):
        """Registra una corrida de bitácora desde la tableta.

        `consumptions`: lista de {input_line_id, consumed_sqm}. Las validaciones
        (no exceder la placa, no producir más de lo consumido, placa única por
        corrida) las aplican las constraints del modelo.
        """
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se registra bitácora en órdenes en taller.'))
        lines = []
        valid_ids = set(self.input_line_ids.ids)
        for raw in consumptions or []:
            try:
                line_id = int(raw.get('input_line_id'))
                qty = float(raw.get('consumed_sqm') or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            if line_id not in valid_ids:
                raise UserError(_('La placa seleccionada no pertenece a esta orden.'))
            if qty <= 0.0:
                continue
            lines.append((0, 0, {'input_line_id': line_id, 'consumed_sqm': qty}))
        if not lines:
            raise UserError(_('Selecciona al menos una placa con m² consumidos.'))
        try:
            area = float(area_sqm or 0.0)
        except (TypeError, ValueError):
            area = 0.0
        if area <= 0.0:
            raise UserError(_('Captura los m² producidos en esta corrida.'))
        vals = {
            'order_id': self.id,
            'area_sqm': area,
            'notes': notes or False,
            'consumption_line_ids': lines,
            'responsible_id': self.env.user.id,
        }
        if date:
            vals['date'] = date
        self.env['workshop.progress.log'].create(vals)
        return self.get_tablet_order_detail()

    def tablet_delete_progress_log(self, log_id):
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se edita la bitácora de órdenes en taller.'))
        log = self.env['workshop.progress.log'].browse(int(log_id)).exists()
        if not log or log.order_id.id != self.id:
            raise UserError(_('Esa corrida no pertenece a esta orden.'))
        log.unlink()
        return self.get_tablet_order_detail()

    def tablet_declare_result(self):
        """Paso 3 desde la tableta: cierra la orden (devuelve no usadas, cuadra merma)."""
        self.ensure_one()
        self.action_declare_result()
        return self.get_tablet_order_detail()

    @api.model
    def tablet_reorder_queue(self, ordered_ids):
        self.reorder_workshop_queue(ordered_ids)
        return True
