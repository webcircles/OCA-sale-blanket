# Copyright 2026 Webcircles
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import freezegun

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import SaleOrderBlanketOrderCase


@tagged("post_install", "-at_install")
class TestInvoiceOnCallOff(SaleOrderBlanketOrderCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uom_hour = cls.env.ref("uom.product_uom_hour")
        cls.service_fixed = cls.env["product.product"].create(
            {
                "name": "Consulting Hours (fixed price)",
                "type": "service",
                "invoice_policy": "order",
                "uom_id": cls.uom_hour.id,
                "uom_po_id": cls.uom_hour.id,
                "list_price": 90.0,
            }
        )
        cls.service_delivered = cls.env["product.product"].create(
            {
                "name": "Consulting Hours (delivered qty)",
                "type": "service",
                "invoice_policy": "delivery",
                "service_type": "manual",
                "uom_id": cls.uom_hour.id,
                "uom_po_id": cls.uom_hour.id,
                "list_price": 90.0,
            }
        )
        # Some databases ship with a deprecated default income account on the
        # product category, which would make invoice creation fail for
        # reasons unrelated to this test. Point the test products to any
        # active income account, if there's one, instead of relying on the
        # category default.
        income_account = cls.env["account.account"].search(
            [("account_type", "=", "income"), ("deprecated", "=", False)], limit=1
        )
        if income_account:
            (cls.service_fixed | cls.service_delivered).property_account_income_id = (
                income_account
            )

    def _create_blanket(self, product, invoice_on_call_off):
        return self.env["sale.order"].create(
            {
                "order_type": "blanket",
                "partner_id": self.partner.id,
                "blanket_validity_start_date": "2025-01-01",
                "blanket_validity_end_date": "2025-12-31",
                "blanket_reservation_strategy": "at_call_off",
                "invoice_on_call_off": invoice_on_call_off,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "name": product.name,
                            "product_uom_qty": 640.0,
                            "price_unit": 90.0,
                        }
                    ),
                ],
            }
        )

    def _create_call_off(self, blanket, product, qty, price_unit):
        return self.env["sale.order"].create(
            {
                "order_type": "call_off",
                "date_order": "2025-02-01",
                "partner_id": self.partner.id,
                "blanket_order_id": blanket.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "name": product.name,
                            "product_uom_qty": qty,
                            "price_unit": price_unit,
                        }
                    ),
                ],
            }
        )

    @freezegun.freeze_time("2025-02-01")
    def test_call_off_price_blocked_by_default(self):
        """Without invoice_on_call_off, the existing price=0 constraint on
        call-off lines must still be enforced (backward compatibility)."""
        blanket = self._create_blanket(self.service_fixed, invoice_on_call_off=False)
        blanket.action_confirm()
        with self.assertRaisesRegex(
            ValidationError, "The price of a call-off order line must be 0.0"
        ):
            self._create_call_off(blanket, self.service_fixed, 320.0, 90.0)

    @freezegun.freeze_time("2025-02-01")
    def test_call_off_fixed_price_invoiced_on_call_off(self):
        """With invoice_on_call_off, a fixed-price (invoice_policy=order)
        call-off is priced and invoiceable on its own, while the blanket
        order line itself becomes non-invoiceable."""
        blanket = self._create_blanket(self.service_fixed, invoice_on_call_off=True)
        blanket.action_confirm()
        call_off = self._create_call_off(blanket, self.service_fixed, 320.0, 90.0)
        call_off.action_confirm()
        self.assertIn(call_off.state, ("sale", "done"))

        call_off_line = call_off.order_line
        self.assertEqual(call_off_line.price_unit, 90.0)
        self.assertTrue(call_off_line.tax_id)
        self.assertEqual(call_off_line.qty_to_invoice, 320.0)
        self.assertEqual(call_off.amount_untaxed, 320.0 * 90.0)

        blanket_line = blanket.order_line
        self.assertEqual(blanket_line.qty_to_invoice, 0.0)
        self.assertEqual(blanket.invoice_status, "no")

        invoices = call_off._create_invoices()
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.amount_untaxed, 320.0 * 90.0)

    @freezegun.freeze_time("2025-02-01")
    def test_call_off_delivered_qty_invoiced_on_call_off(self):
        """With invoice_on_call_off, a delivered-quantity call-off line
        behaves exactly like a regular sale order line: qty_delivered and
        qty_to_invoice are computed normally, not forced to 0."""
        blanket = self._create_blanket(
            self.service_delivered, invoice_on_call_off=True
        )
        blanket.action_confirm()
        call_off = self._create_call_off(
            blanket, self.service_delivered, 320.0, 90.0
        )
        call_off.action_confirm()

        call_off_line = call_off.order_line
        self.assertEqual(call_off_line.qty_delivered, 0.0)
        self.assertEqual(call_off_line.qty_to_invoice, 0.0)

        call_off_line.qty_delivered = 120.0
        self.assertEqual(call_off_line.qty_to_invoice, 120.0)
        self.assertEqual(call_off.invoice_status, "to invoice")

        invoices = call_off._create_invoices()
        self.assertEqual(invoices.amount_untaxed, 120.0 * 90.0)

    @freezegun.freeze_time("2025-02-01")
    def test_amount_total_kpi_excludes_blanket_when_flag_enabled(self):
        """The list/kanban-only amount_total_kpi field must be 0 for a
        blanket order with invoice_on_call_off enabled, while the real
        amount_total is left untouched (used by the printed report, portal,
        down payment, credit limit...)."""
        blanket = self._create_blanket(self.service_fixed, invoice_on_call_off=True)
        blanket.action_confirm()
        self.assertEqual(blanket.amount_untaxed, 640.0 * 90.0)
        self.assertEqual(blanket.amount_total_kpi, 0.0)

        call_off = self._create_call_off(blanket, self.service_fixed, 320.0, 90.0)
        call_off.action_confirm()
        self.assertEqual(call_off.amount_total_kpi, call_off.amount_total)

    @freezegun.freeze_time("2025-02-01")
    def test_sale_report_excludes_blanket_when_flag_enabled(self):
        """sale.report must not include the blanket order line when
        invoice_on_call_off is enabled, to avoid double counting the same
        commercial value once on the blanket and once on the call-off."""
        blanket = self._create_blanket(self.service_fixed, invoice_on_call_off=True)
        blanket.action_confirm()
        call_off = self._create_call_off(blanket, self.service_fixed, 320.0, 90.0)
        call_off.action_confirm()

        report_lines = self.env["sale.report"].search(
            [("order_id", "in", (blanket | call_off).ids)]
        )
        self.assertEqual(report_lines.order_id, call_off)
