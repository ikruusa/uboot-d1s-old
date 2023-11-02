// SPDX-License-Identifier: GPL-2.0+
/*
 * Copyright (C) 2022 Samuel Holland <samuel@sholland.org>
 */

#include <clk-uclass.h>
#include <dm.h>
#include <reset-uclass.h>
#include <asm/io.h>
#include <linux/bitops.h>

struct sun6i_prcm_plat {
	void *base;
};

static int sun6i_prcm_set_field(struct udevice *dev, uint reg,
				u32 mask, bool set)
{
	struct sun6i_prcm_plat *plat = dev_get_plat(dev->parent);

	clrsetbits_le32(plat->base + reg, mask, set ? mask : 0);

	return 0;
}

static int sun6i_prcm_clk_enable(struct clk *clk)
{
	return sun6i_prcm_set_field(clk->dev, 0x28, BIT(clk->id), true);
}

static int sun6i_prcm_clk_disable(struct clk *clk)
{
	return sun6i_prcm_set_field(clk->dev, 0x28, BIT(clk->id), false);
}

static const struct clk_ops sun6i_prcm_clk_ops = {
	.enable		= sun6i_prcm_clk_enable,
	.disable	= sun6i_prcm_clk_disable,
};

static const struct udevice_id sun6i_prcm_clk_ids[] = {
	{ .compatible = "allwinner,sun6i-a31-apb0-gates-clk" },
	{ .compatible = "allwinner,sun8i-a23-apb0-gates-clk" },
	{ }
};

U_BOOT_DRIVER(sun6i_prcm_clk) = {
	.name		= "sun6i_prcm_clk",
	.id		= UCLASS_CLK,
	.of_match	= sun6i_prcm_clk_ids,
	.ops		= &sun6i_prcm_clk_ops,
};

static int sun6i_prcm_reset_assert(struct reset_ctl *reset)
{
	return sun6i_prcm_set_field(reset->dev, 0xb0, BIT(reset->id), false);
}

static int sun6i_prcm_reset_deassert(struct reset_ctl *reset)
{
	return sun6i_prcm_set_field(reset->dev, 0xb0, BIT(reset->id), true);
}

static const struct reset_ops sun6i_prcm_reset_ops = {
	.rst_assert	= sun6i_prcm_reset_assert,
	.rst_deassert	= sun6i_prcm_reset_deassert,
};

static const struct udevice_id sun6i_prcm_reset_ids[] = {
	{ .compatible = "allwinner,sun6i-a31-clock-reset" },
	{ }
};

U_BOOT_DRIVER(sun6i_prcm_reset) = {
	.name		= "sun6i_prcm_reset",
	.id		= UCLASS_RESET,
	.of_match	= sun6i_prcm_reset_ids,
	.ops		= &sun6i_prcm_reset_ops,
};

static int sun6i_prcm_of_to_plat(struct udevice *dev)
{
	struct sun6i_prcm_plat *plat = dev_get_plat(dev);

	plat->base = dev_read_addr_ptr(dev);
	if (!plat->base)
		return -ENOMEM;

	return 0;
}

static const struct udevice_id sun6i_prcm_mfd_ids[] = {
	{ .compatible = "allwinner,sun6i-a31-prcm" },
	{ .compatible = "allwinner,sun8i-a23-prcm" },
	{ }
};

U_BOOT_DRIVER(sun6i_prcm_mfd) = {
	.name		= "sun6i_prcm_mfd",
	.id		= UCLASS_SIMPLE_BUS,
	.of_match	= sun6i_prcm_mfd_ids,
	.of_to_plat	= sun6i_prcm_of_to_plat,
	.plat_auto	= sizeof(struct sun6i_prcm_plat),
};
