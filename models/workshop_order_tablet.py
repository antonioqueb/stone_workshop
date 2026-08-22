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

from .workshop_order import WORKSHOP_PAUSE_REASONS, RESIDUAL_SCRAP_TAG


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
        operators = user.workshop_tablet_operator_ids.filtered('active')
        return {
            'uid': user.id,
            'name': user.name,
            'operators': [o._tablet_payload() for o in operators],
            'lock_minutes': max(0, int(user.workshop_tablet_lock_minutes or 0)),
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
            'product_id': line.product_id.id if line.product_id else 0,
            'input_lot': line.source_lot_id.name if line.source_lot_id else '',
            'input_line_id': line.input_line_id.id if line.input_line_id else 0,
            'is_residual': (line.finish_result or '') == RESIDUAL_SCRAP_TAG,
            'locked': line.state in ('produced', 'received', 'scrapped'),
            'location_dest': line.location_dest_id.display_name if line.location_dest_id else '',
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
            'responsible': (
                log.operator_id.name if log.operator_id
                else (log.responsible_id.name if log.responsible_id else '')
            ),
            'operator_id': log.operator_id.id if log.operator_id else 0,
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
            'responsible': (
                session.operator_id.name if session.operator_id
                else (session.responsible_id.name if session.responsible_id else '')
            ),
            'operator_id': session.operator_id.id if session.operator_id else 0,
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
            'result': self._tablet_result_preview(),
            'is_mine': self.responsible_id.id == self.env.user.id,
            'tablet_operator_id': self.tablet_operator_id.id if self.tablet_operator_id else 0,
            'tablet_operator': self.tablet_operator_id.name if self.tablet_operator_id else '',
        })
        # En el panel, "responsable" es quien realmente opera desde la tableta.
        if self.tablet_operator_id:
            data['responsible'] = self.tablet_operator_id.name
        return data

    # ─── Resultado (salidas) ────────────────────────────────────────────────
    def _tablet_result_preview(self):
        """Balance en vivo para la pantalla "Resultado" de la tableta.

        Mismo cálculo que _ensure_residual_scrap_line pero SIN escribir:
        consumido (placas usadas en bitácora) − útil − subproductos − merma
        manual = merma residual que se materializará al declarar.
        """
        self.ensure_one()
        used = self._get_used_input_lines()
        outputs = self._get_active_output_lines()
        consumed = sum(self._input_line_area(l) for l in used)
        useful = sum(
            self._output_line_area(l) for l in outputs
            if l.output_type in ('finished_slab', 'format_piece')
        )
        remnant = sum(self._output_line_area(l) for l in outputs if l.output_type == 'remnant')
        manual_scrap = sum(
            self._output_line_area(l) for l in outputs
            if l.output_type in ('scrap', 'rejected')
            and (l.finish_result or '') != RESIDUAL_SCRAP_TAG
        )
        residual = consumed - useful - remnant - manual_scrap
        aggregated = self.operation_mode in ('slab_cut', 'format_process')
        logged = sum((l.area_sqm or 0.0) for l in self.progress_log_ids)
        return {
            'aggregated': aggregated,  # corte/formato: el operador declara m²
            'consumed_area': consumed,
            'logged_area': logged,
            'useful_area': useful,
            'remnant_area': remnant,
            'manual_scrap_area': manual_scrap,
            'residual_scrap_area': residual if (aggregated and residual > 0.0001) else 0.0,
            'yield_percent': (useful / consumed * 100.0) if consumed > 0 else 0.0,
            'useful_count': len([l for l in outputs if l.output_type in ('finished_slab', 'format_piece')]),
            'main_product': (
                self.default_product_out_id.display_name if self.default_product_out_id
                else self._workshop_produce_product_label()
            ),
            'remnant_product': self.remnant_product_id.display_name if self.remnant_product_id else '',
        }

    def _tablet_editable_output(self, line_id):
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se editan salidas de órdenes en taller.'))
        line = self.env['workshop.output.line'].browse(int(line_id)).exists()
        if not line or line.order_id.id != self.id:
            raise UserError(_('Esa salida no pertenece a esta orden.'))
        if line.state in ('produced', 'received', 'scrapped', 'cancelled'):
            raise UserError(_('La salida %s ya está cerrada y no se puede editar.') % line.display_name)
        return line

    def tablet_update_output(self, line_id, values, operator_id=False):
        """Edita UNA salida desde la tableta (m², piezas, lote, acabado, dims)."""
        self.ensure_one()
        line = self._tablet_editable_output(line_id)
        allowed = ('area_sqm', 'qty_out', 'pieces', 'lot_name', 'finish_result',
                   'width_cm', 'height_cm', 'thickness_cm')
        vals = {}
        for key in allowed:
            if key in (values or {}):
                v = values[key]
                if key in ('lot_name', 'finish_result'):
                    vals[key] = (v or '').strip() or False
                elif key == 'pieces':
                    vals[key] = int(v or 0)
                else:
                    vals[key] = float(v or 0.0)
        if 'area_sqm' in vals and 'qty_out' not in vals and line.product_id \
                and self._product_uom_is_area(line.product_id):
            vals['qty_out'] = vals['area_sqm']
        if vals:
            line.write(vals)
        return self.get_tablet_order_detail()

    def tablet_add_output(self, kind, values=None, operator_id=False):
        """Agrega una salida: 'guacal' (útil, mismo producto), 'remnant'
        (subproducto) o 'scrap' (merma manual)."""
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se agregan salidas a órdenes en taller.'))
        values = values or {}
        area = float(values.get('area_sqm') or 0.0)
        pieces = int(values.get('pieces') or 0)
        lot_name = (values.get('lot_name') or '').strip() or False
        if kind == 'guacal':
            if self.operation_mode not in ('slab_cut', 'format_process'):
                raise UserError(_('Los guacales aplican en corte / formato.'))
            vals = self._guacal_template_vals()
            vals.update({'lot_name': lot_name, 'area_sqm': area, 'pieces': pieces or 1})
            if vals.get('product_id') and self._product_uom_is_area(
                    self.env['product.product'].browse(vals['product_id'])):
                vals['qty_out'] = area
        elif kind == 'remnant':
            if not self.remnant_product_id:
                raise UserError(_('Esta orden no tiene producto de subproducto configurado.'))
            vals = {
                'output_type': 'remnant',
                'product_id': self.remnant_product_id.id,
                'lot_name': lot_name,
                'area_sqm': area,
                'qty_out': area,
                'pieces': pieces or 1,
            }
        elif kind == 'scrap':
            vals = {
                'output_type': 'scrap',
                'product_id': False,
                'lot_name': False,
                'area_sqm': area,
                'qty_out': 0.0,
                'pieces': 0,
                'finish_result': (values.get('finish_result') or _('Merma declarada en tableta')),
            }
        else:
            raise UserError(_('Tipo de salida desconocido.'))
        self._create_output_line(vals)
        return self.get_tablet_order_detail()

    def tablet_finish(self, lots=None, operator_id=False):
        """TERMINAR ORDEN desde la tableta en un solo paso.

        `lots`: lista de m² obtenidos, uno por lote de salida (p. ej. [22.5] o
        [10, 8.4, 4.1]). Sólo cantidad en la unidad del producto: sin piezas,
        sin folio (el sistema asigna el siguiente libre), sin dimensiones.
        - Corte / formato: se ajustan las salidas útiles al número de lotes
          (se reutilizan las existentes, se crean guacales si faltan, se
          borran las sobrantes) y la merma queda como residual automática.
        - Acabado / reproceso (1:1): `lots` se ignora; la salida de cada placa
          ya la define la bitácora.
        Después declara el resultado y cierra la orden.
        """
        self.ensure_one()
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se termina una orden que está en taller.'))
        op = self._tablet_operator(operator_id)
        if self.operation_mode in ('slab_cut', 'format_process'):
            areas = []
            for raw in (lots or []):
                try:
                    a = float(raw or 0.0)
                except (TypeError, ValueError):
                    continue
                if a > 0.0:
                    areas.append(a)
            if not areas:
                raise UserError(_('Indica cuántos m² obtuviste.'))
            consumed = sum(self._input_line_area(l) for l in self._get_used_input_lines())
            if sum(areas) > consumed + 0.0001:
                raise UserError(_(
                    'Obtuviste %(got).2f m² pero sólo consumiste %(used).2f m².'
                ) % {'got': sum(areas), 'used': consumed})
            useful = self._get_active_output_lines().filtered(
                lambda l: l.output_type in ('finished_slab', 'format_piece')
                and l.state not in ('produced', 'received', 'scrapped')
            ).sorted(lambda l: (l.sequence, l.id))
            # Reutilizar / crear / borrar hasta tener len(areas) salidas útiles
            while len(useful) < len(areas):
                vals = self._guacal_template_vals()
                vals.update({'lot_name': False, 'area_sqm': 0.0, 'qty_out': 0.0, 'pieces': 1})
                useful |= self._create_output_line(vals)
                useful = useful.sorted(lambda l: (l.sequence, l.id))
            extra = useful[len(areas):]
            if extra:
                extra.unlink()
                useful = useful[:len(areas)]
            for line, area in zip(useful, areas):
                vals = {'area_sqm': area, 'pieces': 1}
                if line.product_id and self._product_uom_is_area(line.product_id):
                    vals['qty_out'] = area
                line.write(vals)
            # La merma y subproductos manuales no se capturan en tableta:
            # todo el sobrante cierra como merma residual automática.
            manual = self._get_active_output_lines().filtered(
                lambda l: l.output_type in ('remnant', 'scrap', 'rejected')
                and (l.finish_result or '') != RESIDUAL_SCRAP_TAG
                and l.state not in ('produced', 'received', 'scrapped')
            )
            if manual:
                manual.unlink()
        self.action_declare_result()
        self._tablet_stamp(op, _('Orden terminada'))
        return self.get_tablet_order_detail()

    def tablet_delete_output(self, line_id, operator_id=False):
        self.ensure_one()
        line = self._tablet_editable_output(line_id)
        line.unlink()
        return self.get_tablet_order_detail()

    # ─── Operador (login compartido) ────────────────────────────────────────
    def _workshop_board_payload_extend(self, data):
        data = super()._workshop_board_payload_extend(data)
        if self.tablet_operator_id:
            data['responsible'] = self.tablet_operator_id.name
        return data

    def _tablet_operator(self, operator_id):
        """Operador válido para el usuario actual o vacío."""
        if not operator_id:
            return self.env['workshop.tablet.operator']
        try:
            op = self.env['workshop.tablet.operator'].browse(int(operator_id)).exists()
        except (TypeError, ValueError):
            return self.env['workshop.tablet.operator']
        if op and op.user_id.id != self.env.user.id:
            raise UserError(_('Ese operador no pertenece a este usuario de tableta.'))
        return op

    def _tablet_stamp(self, op, what):
        """Deja rastro de quién hizo la acción (OT, sesión abierta, chatter)."""
        self.ensure_one()
        if not op:
            return
        vals = {'tablet_operator_id': op.id}
        if op.linked_user_id:
            vals['responsible_id'] = op.linked_user_id.id
        self.write(vals)
        open_session = self.work_session_ids.filtered(lambda s: not s.end)[:1]
        if open_session and not open_session.operator_id:
            open_session.operator_id = op.id
        self.message_post(body=_('%(what)s — operador: %(op)s (tableta)') % {
            'what': what, 'op': op.name,
        })

    # ─── Acciones ───────────────────────────────────────────────────────────
    def tablet_start(self, operator_id=False):
        """Mover de la cola a ejecución.

        Borrador → confirma al taller (consume material y arranca el reloj).
        Estacionada por la regla de 24 h → la retoma (sale de la cola).
        """
        self.ensure_one()
        op = self._tablet_operator(operator_id)
        if self.state == 'draft':
            self.action_confirm_workshop()
            self._tablet_stamp(op, _('Orden iniciada'))
        elif self.state == 'in_workshop':
            if not self.timer_running:
                self.action_resume_timer()
            self._tablet_stamp(op, _('Orden retomada'))
        else:
            raise UserError(_('La orden %s ya no se puede iniciar (%s).') % (
                self.name, dict(self._fields['state'].selection).get(self.state, self.state)
            ))
        return self.get_tablet_order_detail()

    def tablet_pause(self, reason=False, note=False, operator_id=False):
        self.ensure_one()
        op = self._tablet_operator(operator_id)
        valid = {code for code, _label in WORKSHOP_PAUSE_REASONS}
        if reason and reason not in valid:
            reason = 'other'
        # Estampar ANTES de cerrar la sesión para que quede quién pausó.
        if op:
            open_session = self.work_session_ids.filtered(lambda s: not s.end)[:1]
            if open_session and not open_session.operator_id:
                open_session.operator_id = op.id
        self.action_pause_timer(reason=reason or False, note=note or False)
        self._tablet_stamp(op, _('Reloj pausado'))
        return self.get_tablet_order_detail()

    def tablet_resume(self, operator_id=False):
        self.ensure_one()
        op = self._tablet_operator(operator_id)
        self.action_resume_timer()
        self._tablet_stamp(op, _('Reloj reanudado'))
        return self.get_tablet_order_detail()

    def tablet_take(self, operator_id=False):
        """El operador se asigna como responsable de la orden."""
        self.ensure_one()
        if self.state in ('done', 'cancel'):
            raise UserError(_('La orden %s ya está cerrada.') % self.name)
        op = self._tablet_operator(operator_id)
        if op:
            self._tablet_stamp(op, _('Orden tomada'))
            if not op.linked_user_id:
                self.write({'responsible_id': self.env.user.id})
        else:
            self.write({'responsible_id': self.env.user.id})
            self.message_post(body=_('Orden tomada desde tableta por %s.') % self.env.user.name)
        return self.get_tablet_order_detail()

    def tablet_add_progress_log(self, area_sqm, consumptions, notes=False, date=False, operator_id=False):
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
        op = self._tablet_operator(operator_id)
        vals = {
            'order_id': self.id,
            'area_sqm': area,
            'notes': notes or False,
            'consumption_line_ids': lines,
            'responsible_id': (op.linked_user_id.id if op and op.linked_user_id else self.env.user.id),
            'operator_id': op.id if op else False,
        }
        if date:
            vals['date'] = date
        self.env['workshop.progress.log'].create(vals)
        self._tablet_stamp(op, _('Corrida registrada (%.2f m²)') % area)
        return self.get_tablet_order_detail()

    def tablet_delete_progress_log(self, log_id, operator_id=False):
        self.ensure_one()
        op = self._tablet_operator(operator_id)
        if self.state != 'in_workshop':
            raise UserError(_('Sólo se edita la bitácora de órdenes en taller.'))
        log = self.env['workshop.progress.log'].browse(int(log_id)).exists()
        if not log or log.order_id.id != self.id:
            raise UserError(_('Esa corrida no pertenece a esta orden.'))
        log_desc = '%s · %.2f m²' % (log.date, log.area_sqm or 0.0)
        log.unlink()
        self._tablet_stamp(op, _('Corrida borrada (%s)') % log_desc)
        return self.get_tablet_order_detail()

    def tablet_declare_result(self, operator_id=False):
        """Paso 3 desde la tableta: cierra la orden (devuelve no usadas, cuadra merma)."""
        self.ensure_one()
        op = self._tablet_operator(operator_id)
        self.action_declare_result()
        self._tablet_stamp(op, _('Resultado declarado'))
        return self.get_tablet_order_detail()

    @api.model
    def tablet_reorder_queue(self, ordered_ids):
        self.reorder_workshop_queue(ordered_ids)
        return True
