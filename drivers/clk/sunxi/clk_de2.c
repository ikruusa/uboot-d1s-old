// SPDX-License-Identifier: (GPL-2.0+ OR MIT)
/*
 * Copyright (C) 2022 Samuel Holland <samuel@sholland.org>
 */

#include <clk/sunxi.h>
#include <dt-bindings/clock/sun8i-de2.h>
#include <dt-bindings/reset/sun8i-de2.h>
#include <linux/bitops.h>

static struct ccu_clk_gate de2_gates[] = {
	[CLK_MIXER0]		= GATE(0x00, BIT(0)),
	[CLK_MIXER1]		= GATE(0x00, BIT(1)),
	[CLK_WB]		= GATE(0x00, BIT(2)),
	[CLK_ROT]		= GATE(0x00, BIT(3)),

	[CLK_BUS_MIXER0]	= GATE(0x04, BIT(0)),
	[CLK_BUS_MIXER1]	= GATE(0x04, BIT(1)),
	[CLK_BUS_WB]		= GATE(0x04, BIT(2)),
	[CLK_BUS_ROT]		= GATE(0x04, BIT(3)),
};

static struct ccu_reset de2_resets[] = {
	[RST_MIXER0]		= RESET(0x08, BIT(0)),
	[RST_MIXER1]		= RESET(0x08, BIT(1)),
	[RST_WB]		= RESET(0x08, BIT(2)),
	[RST_ROT]		= RESET(0x08, BIT(3)),
};

const struct ccu_desc de2_ccu_desc = {
	.gates		= de2_gates,
	.resets		= de2_resets,
	.num_gates	= ARRAY_SIZE(de2_gates),
	.num_resets	= ARRAY_SIZE(de2_resets),
};
