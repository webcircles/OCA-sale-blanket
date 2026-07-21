# Copyright 2026 Webcircles
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class SaleReport(models.Model):
    _inherit = "sale.report"

    def _where_sale(self):
        # When a blanket order has invoice_on_call_off enabled, its own lines
        # are a non-invoiceable reference (the real, priced commercial value
        # lives on the linked call-off orders). Exclude them from the Sales
        # Analysis report to avoid double counting the same commercial value
        # once on the blanket order and once on each call-off order.
        return (
            super()._where_sale()
            + """
            AND NOT (s.order_type = 'blanket' AND s.invoice_on_call_off IS TRUE)"""
        )
