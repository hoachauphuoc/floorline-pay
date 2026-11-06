import { cn } from "@/lib/utils";

export const PAYPAL_LOGO_SRC = "/paypal/paypal-logo.png";

/**
 * The PayPal mark, shown as the official file from public/paypal and never redrawn or recoloured.
 * It is a plain img: the page's CSP only allows images from the site itself, and the file is a PNG.
 * Pass `decorative` when the text next to it already says PayPal, so a screen reader hears it once.
 */
export function PayPalLogo({
  height = 18,
  decorative = false,
  className,
}: {
  height?: number;
  decorative?: boolean;
  className?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={PAYPAL_LOGO_SRC}
      alt={decorative ? "" : "PayPal"}
      style={{ height, width: "auto" }}
      className={cn("inline-block shrink-0", className)}
    />
  );
}
