# -*- coding: utf-8 -*-
"""Flujo del taller tras el cambio del 30 sep 2026.

* Confirmar ≠ iniciar: confirmar no mueve material ni arranca el reloj.
* Iniciar consume el material (sale del almacén) y arranca el cronómetro.
* El ticket ya no "marca todo como consumido".
* Un parcial que consume todo el material se rechaza (es entrega completa).
* En corte/formato la salida capturada a mano manda sobre la bitácora.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import WorkshopCase


@tagged('post_install', '-at_install', 'stone_workshop')
class TestWorkshopConfirmStart(WorkshopCase):

    def test_01_confirm_does_not_move_material_nor_start_clock(self):
        lot = self.make_lot('PRB-T01', 5.0)
        order = self.make_order(self.p_finish, [(lot, 5.0)])
        order.action_confirm_workshop()
        self.assertEqual(order.state, 'confirmed')
        self.assertTrue(order.date_confirmed)
        self.assertFalse(order.date_start)
        self.assertFalse(order.work_session_ids, 'Confirmar no debe abrir sesión de reloj')
        self.assertFalse(order.timer_running)
        self.assertFalse(order.consume_picking_ids, 'Confirmar no debe crear picking de consumo')
        self.assertFalse(order.input_line_ids.filtered('is_consumed'))
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 5.0, places=3,
                               msg='La placa sigue en el almacén tras confirmar')
        self.assertTrue(order.output_line_ids, 'Confirmar pre-llena las salidas sugeridas')
        self.assertTrue(order.material_ready)

    def test_02_start_consumes_and_starts_clock(self):
        lot = self.make_lot('PRB-T02', 5.0)
        order = self.make_order(self.p_finish, [(lot, 5.0)])
        order.action_confirm_workshop()
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')
        self.assertTrue(order.date_start)
        self.assertTrue(order.timer_running)
        self.assertEqual(len(order.work_session_ids), 1)
        self.assertTrue(all(order.input_line_ids.mapped('is_consumed')))
        self.assertEqual(len(order.consume_picking_ids), 1)
        self.assertEqual(order.consume_picking_ids.state, 'done')
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 0.0, places=3,
                               msg='La placa consumida ya no está en el almacén')
        self.assertAlmostEqual(self.qty_at(lot, order.location_workshop_id), 5.0, places=3)

    def test_03_start_from_draft_confirms_and_starts(self):
        lot = self.make_lot('PRB-T03', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')
        self.assertTrue(order.date_confirmed)
        self.assertTrue(order.timer_running)

    def test_04_cannot_start_twice_or_confirm_twice(self):
        lot = self.make_lot('PRB-T04', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_confirm_workshop()
        with self.assertRaises(UserError):
            order.action_confirm_workshop()
        order.action_start_workshop()
        with self.assertRaises(UserError):
            order.action_start_workshop()

    def test_05_unconfirm_back_to_draft(self):
        lot = self.make_lot('PRB-T05', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_confirm_workshop()
        order.action_draft()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.date_confirmed)
        # Ya iniciada no puede regresar a borrador.
        order.action_start_workshop()
        with self.assertRaises(UserError):
            order.action_draft()

    def test_06_cancel_confirmed(self):
        lot = self.make_lot('PRB-T06', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_confirm_workshop()
        order.action_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 4.0, places=3)

    def test_07_pause_resume_only_in_workshop(self):
        lot = self.make_lot('PRB-T07', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_confirm_workshop()
        with self.assertRaises(UserError):
            order.action_pause_timer()
        with self.assertRaises(UserError):
            order.action_resume_timer()
        order.action_start_workshop()
        order.action_pause_timer()
        self.assertFalse(order.timer_running)
        order.action_resume_timer()
        self.assertTrue(order.timer_running)
        self.assertEqual(len(order.work_session_ids), 2)

    def test_08_board_and_tablet(self):
        lot = self.make_lot('PRB-T08', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.action_confirm_workshop()
        board = self.env['workshop.order'].get_workshop_board()
        queue_ids = [o['id'] for o in board['queue']]
        self.assertIn(order.id, queue_ids, 'Las confirmadas aparecen en la cola')
        card = [o for o in board['queue'] if o['id'] == order.id][0]
        self.assertEqual(card['state'], 'confirmed')
        self.assertTrue(card['material_ready'])
        detail = order.get_tablet_order_detail()
        self.assertTrue(detail['can_start'])
        self.assertFalse(detail['can_confirm'])
        detail = order.tablet_start()
        self.assertEqual(order.state, 'in_workshop')
        self.assertIn(order.id, [o['id'] for o in self.env['workshop.order'].get_workshop_board()['execution']])
        self.env['workshop.order'].get_workshop_kpis()

    def test_09_tablet_start_from_draft(self):
        lot = self.make_lot('PRB-T09', 4.0)
        order = self.make_order(self.p_finish, [(lot, 4.0)])
        order.tablet_start()
        self.assertEqual(order.state, 'in_workshop')
        self.assertTrue(order.timer_running)


@tagged('post_install', '-at_install', 'stone_workshop')
class TestWorkshopTicket(WorkshopCase):

    def test_10_no_mark_all_consumed(self):
        lot1 = self.make_lot('PRB-K01', 5.0)
        lot2 = self.make_lot('PRB-K02', 5.0)
        order = self.make_order(self.p_finish, [(lot1, 5.0), (lot2, 5.0)])
        order.action_start_workshop()
        ticket = order._workshop_auto_ticket_all_inputs()
        self.assertTrue(ticket)
        self.assertEqual(ticket.state, 'prepared')
        with self.assertRaises(UserError):
            ticket.action_mark_consumed()
        self.assertEqual(ticket.state, 'prepared')
        self.assertFalse(order.progress_log_ids, 'Ninguna corrida inventada por el ticket')

    def test_11_wizard_generate_and_consume_removed(self):
        lot = self.make_lot('PRB-K03', 5.0)
        order = self.make_order(self.p_finish, [(lot, 5.0)])
        order.action_start_workshop()
        wizard = self.env['workshop.ticket.wizard'].with_context(
            default_order_id=order.id, active_id=order.id, active_model='workshop.order').create({})
        with self.assertRaises(UserError):
            wizard.action_generate_and_consume_ticket()
        self.assertFalse(order.progress_log_ids)


@tagged('post_install', '-at_install', 'stone_workshop')
class TestWorkshopPartials(WorkshopCase):

    def test_20_finish_partial_ok_when_material_remains(self):
        lot1 = self.make_lot('PRB-P01', 5.0)
        lot2 = self.make_lot('PRB-P02', 5.0)
        order = self.make_order(self.p_finish, [(lot1, 5.0), (lot2, 5.0)])
        order.action_start_workshop()
        l1 = order.input_line_ids.filtered(lambda l: l.lot_id == lot1)
        self.log(order, [(l1, 5.0)], 5.0)
        order.action_declare_partial()
        self.assertEqual(order.state, 'in_workshop')
        received = order.output_line_ids.filtered(lambda o: o.state == 'received')
        self.assertEqual(len(received), 1)

    def test_21_finish_partial_rejected_when_everything_consumed(self):
        lot1 = self.make_lot('PRB-P03', 5.0)
        lot2 = self.make_lot('PRB-P04', 5.0)
        order = self.make_order(self.p_finish, [(lot1, 5.0), (lot2, 5.0)])
        order.action_start_workshop()
        lines = order.input_line_ids
        self.log(order, [(l, 5.0) for l in lines], 10.0)
        with self.assertRaisesRegex(UserError, 'consumiendo todo'):
            order.action_declare_partial()
        self.assertFalse(order.output_line_ids.filtered(lambda o: o.state == 'received'))
        # La salida correcta es declarar resultado.
        order.action_declare_result()
        self.assertEqual(order.state, 'done')
        self.assertFalse(order.timer_running)

    def test_22_cut_partial_rejected_when_everything_consumed(self):
        lot1 = self.make_lot('PRB-P05', 5.0)
        order = self.make_order(self.p_cut, [(lot1, 5.0)])
        order.action_start_workshop()
        self.log(order, [(order.input_line_ids, 5.0)], 4.0)
        with self.assertRaisesRegex(UserError, 'consumiendo todo'):
            order.action_declare_partial()

    def test_23_cut_partial_consumed_partially_is_allowed(self):
        lot1 = self.make_lot('PRB-P06', 10.0)
        order = self.make_order(self.p_cut, [(lot1, 10.0)])
        order.action_start_workshop()
        self.log(order, [(order.input_line_ids, 6.0)], 5.0)
        order.action_declare_partial()
        received = order.output_line_ids.filtered(
            lambda o: o.state == 'received' and o.output_type in ('finished_slab', 'format_piece'))
        self.assertAlmostEqual(sum(received.mapped('area_sqm')), 5.0, places=3,
                               msg='Sin captura manual manda la bitácora')
        self.assertAlmostEqual(order.input_line_ids.remaining_sqm, 4.0, places=3)


@tagged('post_install', '-at_install', 'stone_workshop')
class TestWorkshopManualOutput(WorkshopCase):

    def _cut_order_in_workshop(self, name, qty):
        lot = self.make_lot(name, qty)
        order = self.make_order(self.p_cut, [(lot, qty)])
        order.action_start_workshop()
        return order

    def _main_outputs(self, order):
        return order.output_line_ids.filtered(
            lambda o: o.state != 'cancelled' and o.output_type in ('finished_slab', 'format_piece'))

    def test_30_system_outputs_are_not_manual(self):
        order = self._cut_order_in_workshop('PRB-M01', 10.0)
        self.assertTrue(self._main_outputs(order))
        self.assertFalse(any(self._main_outputs(order).mapped('manual_capture')),
                         'El plan sugerido no es captura manual')

    def test_31_user_edit_marks_manual(self):
        order = self._cut_order_in_workshop('PRB-M02', 10.0)
        out = self._main_outputs(order)[:1]
        out.write({'area_sqm': 2.0, 'qty_out': 2.0})
        self.assertTrue(out.manual_capture)
        sys_line = order._create_output_line({
            'output_type': 'scrap', 'product_id': False, 'qty_out': 0.0, 'area_sqm': 1.0})
        self.assertFalse(sys_line.manual_capture)

    def test_32_partial_respects_manual_output(self):
        """Caso del cliente: consumí 15 (aquí 6 de 10), capturé 5 (aquí 2):
        sale MI salida de 2 y la diferencia es merma; no un lote por lo consumido."""
        order = self._cut_order_in_workshop('PRB-M03', 10.0)
        manual = self._main_outputs(order)[:1]
        manual.write({'area_sqm': 2.0, 'qty_out': 2.0})
        # La bitácora trae "producido" = consumido (default típico).
        self.log(order, [(order.input_line_ids, 6.0)], 6.0)
        order.action_declare_partial()
        manual.invalidate_recordset()
        self.assertEqual(manual.state, 'received', 'La salida capturada a mano sale tal cual')
        self.assertAlmostEqual(manual.area_sqm, 2.0, places=3)
        self.assertTrue(manual.lot_id)
        received = order.output_line_ids.filtered(
            lambda o: o.state == 'received' and o.output_type in ('finished_slab', 'format_piece'))
        self.assertEqual(received, manual, 'No se genera otro lote con los m² de la bitácora')
        scrap = order.output_line_ids.filtered(
            lambda o: o.state == 'scrapped' and o.output_type == 'scrap')
        self.assertAlmostEqual(sum(scrap.mapped('area_sqm')), 4.0, places=3,
                               msg='Merma = consumido 6 − obtenido 2')
        self.assertAlmostEqual(self.qty_at(manual.lot_id, order.location_dest_id), 2.0, places=3)

    def test_33_manual_output_cannot_exceed_consumed(self):
        order = self._cut_order_in_workshop('PRB-M04', 10.0)
        manual = self._main_outputs(order)[:1]
        manual.write({'area_sqm': 8.0, 'qty_out': 8.0})
        self.log(order, [(order.input_line_ids, 6.0)], 6.0)
        with self.assertRaises(UserError):
            order.action_declare_partial()
        self.assertNotEqual(manual.state, 'received')

    def test_34_declare_result_respects_manual_output(self):
        order = self._cut_order_in_workshop('PRB-M05', 10.0)
        manual = self._main_outputs(order)[:1]
        manual.write({'area_sqm': 3.0, 'qty_out': 3.0})
        self.log(order, [(order.input_line_ids, 10.0)], 10.0)
        order.action_declare_result()
        self.assertEqual(order.state, 'done')
        manual.invalidate_recordset()
        self.assertEqual(manual.state, 'received')
        self.assertAlmostEqual(manual.area_sqm, 3.0, places=3)
        scrap = order.output_line_ids.filtered(lambda o: o.output_type == 'scrap' and o.state == 'scrapped')
        self.assertAlmostEqual(sum(scrap.mapped('area_sqm')), 7.0, places=3)

    def test_35_partial_then_result_full_cycle(self):
        order = self._cut_order_in_workshop('PRB-M06', 10.0)
        line = order.input_line_ids
        self.log(order, [(line, 4.0)], 3.5)
        order.action_declare_partial()
        self.assertEqual(order.state, 'in_workshop')
        self.log(order, [(line, 6.0)], 5.0)
        order.action_declare_result()
        self.assertEqual(order.state, 'done')
        useful = order.output_line_ids.filtered(
            lambda o: o.state == 'received' and o.output_type in ('finished_slab', 'format_piece'))
        self.assertAlmostEqual(sum(useful.mapped('area_sqm')), 8.5, places=3)
        self.assertAlmostEqual(self.qty_at(line.lot_id, self.stock_loc), 0.0, places=3)


@tagged('post_install', '-at_install', 'stone_workshop')
class TestWorkshopSafety(WorkshopCase):

    def test_40_unused_slab_returns_to_stock_on_result(self):
        lot1 = self.make_lot('PRB-X01', 5.0)
        lot2 = self.make_lot('PRB-X02', 5.0)
        order = self.make_order(self.p_finish, [(lot1, 5.0), (lot2, 5.0)])
        order.action_start_workshop()
        self.assertAlmostEqual(self.qty_at(lot2, self.stock_loc), 0.0, places=3)
        l1 = order.input_line_ids.filtered(lambda l: l.lot_id == lot1)
        self.log(order, [(l1, 5.0)], 5.0)
        order.action_declare_result()
        self.assertEqual(order.state, 'done')
        self.assertAlmostEqual(self.qty_at(lot2, self.stock_loc), 5.0, places=3,
                               msg='La placa que no se usó regresa íntegra al almacén')
        self.assertTrue(order.return_picking_ids)

    def test_41_same_slab_cannot_be_in_two_active_orders(self):
        lot = self.make_lot('PRB-X03', 5.0)
        first = self.make_order(self.p_finish, [(lot, 5.0)])
        first.action_start_workshop()
        second = self.make_order(self.p_finish, [(lot, 5.0)])
        with self.assertRaises(UserError):
            second.action_start_workshop()

    def test_42_bitacora_cannot_consume_more_than_slab(self):
        lot = self.make_lot('PRB-X04', 5.0)
        order = self.make_order(self.p_cut, [(lot, 5.0)])
        order.action_start_workshop()
        with self.assertRaises(UserError):
            self.log(order, [(order.input_line_ids, 6.0)], 5.0)

    def test_43_bitacora_cannot_produce_more_than_consumed(self):
        lot = self.make_lot('PRB-X05', 5.0)
        order = self.make_order(self.p_cut, [(lot, 5.0)])
        order.action_start_workshop()
        with self.assertRaises(UserError):
            self.log(order, [(order.input_line_ids, 3.0)], 4.0)
