/**
 * The booking form of Floorline Pay, and the one piece of state that crosses PayPal.
 *
 * A shipper fills the form, is sent to PayPal to approve the payment, and comes back to
 * /book/return. The form is kept in sessionStorage under the PayPal order id so the return
 * page can submit the shipment with the authorization the approval produced. Nothing is
 * trusted from the return address except the order id, and that must equal the stored one.
 */

export const BOOKING_KEY = "floorline-booking";

export interface BookingForm {
  shipment_id: string;
  shipper_company: string;
  shipper_tax_id: string;
  shipper_country: string;
  receiver_company: string;
  receiver_country: string;
  origin: string;
  destination: string;
  cargo_description: string;
  hs_code: string;
  weight_kg: string;
  declared_value: string;
  shipping_cost: string;
  carrier_name: string;
  carrier_payee_email: string;
  payout_amount: string;
}

export const EMPTY_BOOKING: BookingForm = {
  shipment_id: "",
  shipper_company: "",
  shipper_tax_id: "",
  shipper_country: "",
  receiver_company: "",
  receiver_country: "",
  origin: "",
  destination: "",
  cargo_description: "",
  hs_code: "",
  weight_kg: "",
  declared_value: "",
  shipping_cost: "",
  carrier_name: "",
  carrier_payee_email: "",
  payout_amount: "",
};

const NUMERIC = new Set(["weight_kg", "declared_value", "shipping_cost", "payout_amount"]);

/** The reason this form cannot be booked yet, or null. */
export function bookingProblem(form: BookingForm): string | null {
  if (!form.shipment_id.trim()) return "A shipment id is needed.";
  const freight = Number(form.shipping_cost.trim());
  if (!form.shipping_cost.trim() || !Number.isFinite(freight) || freight <= 0) {
    return "The freight to pay must be a number above zero.";
  }
  if (!form.shipper_company.trim()) {
    return "The shipper is needed: a block on a shipper is looked up by its name.";
  }
  return null;
}

/**
 * The shipment event to submit once the payment is authorized.
 *
 * Blank fields are left out, not sent empty: an absent field and an empty one produce
 * different findings. Numbers go as numbers. A blank payout amount is the booked freight,
 * the normal case; a payout above the freight has to be typed, and is then a finding.
 */
export function shipmentFromForm(form: BookingForm): Record<string, unknown> {
  const payload: Record<string, unknown> = { currency: "USD" };
  for (const [key, value] of Object.entries(form)) {
    const trimmed = value.trim();
    if (!trimmed) continue;
    payload[key] = NUMERIC.has(key) ? Number(trimmed) : trimmed;
  }
  if (payload.payout_amount === undefined && payload.shipping_cost !== undefined) {
    payload.payout_amount = payload.shipping_cost;
  }
  return payload;
}

/** The request that creates the PayPal order the shipper approves. */
export function orderRequest(form: BookingForm, origin: string) {
  return {
    amount: Number(form.shipping_cost.trim()),
    currency: "USD",
    reference_id: form.shipment_id.trim(),
    return_url: `${origin}/book/return`,
    cancel_url: `${origin}/book`,
    booking: { ...form, shipment_id: form.shipment_id.trim() },
  };
}

function store(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function saveBooking(orderId: string, form: BookingForm, storage: Storage | null = store()): void {
  storage?.setItem(BOOKING_KEY, JSON.stringify({ order_id: orderId, form }));
}

/** The stored form, only when it belongs to this order; otherwise null. */
export function loadBooking(orderId: string, storage: Storage | null = store()): BookingForm | null {
  const raw = storage?.getItem(BOOKING_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { order_id?: unknown; form?: unknown };
    if (parsed.order_id !== orderId || typeof parsed.form !== "object" || parsed.form === null) {
      return null;
    }
    const form = parsed.form as Record<string, unknown>;
    const clean: BookingForm = { ...EMPTY_BOOKING };
    for (const key of Object.keys(EMPTY_BOOKING) as Array<keyof BookingForm>) {
      const value = form[key];
      if (typeof value === "string") clean[key] = value;
    }
    return clean;
  } catch {
    return null;
  }
}

export function clearBooking(storage: Storage | null = store()): void {
  storage?.removeItem(BOOKING_KEY);
}
