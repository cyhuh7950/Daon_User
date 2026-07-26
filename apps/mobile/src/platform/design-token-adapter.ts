import designTokens from "@daon-user/design-tokens/tokens.json" with { type: "json" };

export function toDeviceIndependentPixels(value: string): number {
  if (!/^\d+(?:\.\d+)?px$/.test(value)) throw new Error(`UNSUPPORTED_DESIGN_TOKEN_LENGTH:${value}`);
  return Number(value.slice(0, -2));
}

const typography = designTokens.typography;
const palette = designTokens.color.palette;

export const mobileTokens = Object.freeze({
  typography: Object.freeze({
    body: toDeviceIndependentPixels(typography.body),
    form: toDeviceIndependentPixels(typography.form),
    description: toDeviceIndependentPixels(typography.description),
    auxiliary: toDeviceIndependentPixels(typography.auxiliary),
    sidebarTitle: toDeviceIndependentPixels(typography.sidebar_title),
    screenTitle: toDeviceIndependentPixels(typography.screen_title)
  }),
  spacing: Object.freeze(designTokens.spacing.map(toDeviceIndependentPixels)),
  radius: Object.freeze(designTokens.radius.map(toDeviceIndependentPixels)),
  targetSize: Object.freeze({
    minimum: toDeviceIndependentPixels(designTokens.target_size.minimum),
    desktopControl: toDeviceIndependentPixels(designTokens.target_size.desktop_control),
    touchControl: toDeviceIndependentPixels(designTokens.target_size.touch_control)
  }),
  color: Object.freeze({
    canvas: palette.canvas,
    surface: palette.surface,
    mutedSurface: palette.muted_surface,
    primaryText: palette.primary_text,
    secondaryText: palette.secondary_text,
    border: palette.border,
    action: palette.accent,
    focus: palette.focus,
    success: palette.success,
    warning: palette.warning,
    danger: palette.danger,
    authority: palette.ruleset_authority
  }),
  status: Object.freeze({
    requiresLabel: designTokens.status.requires_label,
    requiresIcon: designTokens.status.requires_icon,
    requiresText: designTokens.status.requires_text,
    colorOnlyForbidden: designTokens.status.color_only_forbidden
  })
});
