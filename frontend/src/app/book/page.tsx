"use client";

import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { PageHeading } from "@/components/layout/PageHeading";
import { ErrorState } from "@/components/layout/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PayPalLogo } from "@/components/ui/PayPalLogo";
import { Label } from "@/components/ui/label";
import { createPaymentOrder } from "@/lib/api";
import {
  EMPTY_BOOKING,
  bookingFromQuery,
  bookingProblem,
  orderRequest,
  saveBooking,
  type BookingForm,
} from "@/lib/booking";

const FIELDS: Array<[keyof BookingForm, string, string]> = [
  ["shipment_id", "Shipment id", "SHP-PAY-1"],
  ["shipper_company", "Shipper", "Acme Trading Ltd"],
  ["shipper_tax_id", "Shipper tax ID", "0301234567"],
  ["shipper_country", "Shipper country", "Vietnam"],
  ["receiver_company", "Receiver company", "Hanoi Garment Retail Co"],
  ["receiver_country", "Receiver country", "Vietnam"],
  ["origin", "Origin", "ho chi minh city"],
  ["destination", "Destination", "hanoi"],
  ["cargo_description", "Goods", "plywood"],
  ["hs_code", "HS code", "4412.33"],
  ["weight_kg", "Weight (kg)", "22000"],
  ["declared_value", "Declared value (USD)", "28000"],
  ["shipping_cost", "Freight to pay (USD)", "1300"],
  ["carrier_name", "Carrier", "Mekong Haulage Co"],
  ["carrier_payee_email", "Carrier PayPal account for this load", "payments@mekong-haulage.example"],
  ["payout_amount", "Payout to the carrier (USD)", "blank pays the freight"],
];

/**
 * Book a shipment and pay for it with PayPal.
 *
 * The shipper's money is held, not taken: the order is made with an authorization, the
 * shipper approves it on PayPal, and /book/return finishes the booking. The carrier is paid
 * only after a person has released the shipment, by the settler, never from this page.
 */
export default function BookPage() {
  const [form, setForm] = useState<BookingForm>(() => ({
    ...EMPTY_BOOKING,
    shipment_id: `SHP-PAY-${Date.now().toString(36).toUpperCase()}`,
    ...(typeof window === "undefined" ? {} : bookingFromQuery(window.location.search ?? "")),
  }));
  const problem = bookingProblem(form);

  const pay = useMutation({
    mutationFn: async () => {
      const order = await createPaymentOrder(orderRequest(form, window.location.origin));
      saveBooking(order.order_id, form);
      window.location.assign(order.approval_url);
    },
  });

  return (
    <>
      <PageHeading title="Book and pay">
        Pay the freight with PayPal. The money is held while Floorline checks the shipment, paid
        to the carrier only on a published decision (a person&apos;s, or the agent&apos;s inside a limit a person set), and returned to you if it is blocked.
      </PageHeading>

      <section className="bento-card max-w-3xl p-4" aria-label="Booking">
        <div className="grid gap-2 sm:grid-cols-2">
          {FIELDS.map(([key, label, placeholder]) => (
            <div key={key}>
              <Label htmlFor={`b-${key}`} className="text-[11px] text-dim">
                {label}
              </Label>
              <Input
                id={`b-${key}`}
                value={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                placeholder={placeholder}
                className="mt-1 h-8 border-white/10 bg-black/30 text-[12px]"
              />
            </div>
          ))}
        </div>

        {problem && <p className="mt-3 text-[11.5px] text-risk-warn">{problem}</p>}

        <Button
          size="sm"
          disabled={pay.isPending || problem !== null}
          onClick={() => pay.mutate()}
          className="mt-3 h-8 bg-[#FFC439] text-[12px] font-semibold text-[#111111] hover:bg-[#f2b72f]"
        >
          {pay.isPending ? (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
          ) : (
            <PayPalLogo decorative height={14} className="mr-1.5" />
          )}
          Pay with PayPal
        </Button>

        <p className="mt-2 flex items-center gap-2 text-[11px] text-dim">
          <span className="inline-flex h-6 items-center rounded-md bg-white px-2">
            <PayPalLogo height={14} />
          </span>
          Checkout runs on the PayPal sandbox: no real money moves.
        </p>

        {pay.isError && (
          <div className="mt-3">
            <ErrorState error={pay.error} />
          </div>
        )}
      </section>
    </>
  );
}
