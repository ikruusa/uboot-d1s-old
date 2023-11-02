// SPDX-License-Identifier: GPL-2.0+

#define DEBUG
#include <common.h>
#include <dm.h>
#include <ram.h>
#include <asm/io.h>
#include <linux/delay.h>

#include "dram_v2.h"
#include "sdram.h"

#if defined(CONFIG_SPL_BUILD)

int init_DRAM(int type, struct dram_para_t *para);

void __usdelay(unsigned long us)
{
	udelay(us);
}

struct sunxi_ram_priv {
	size_t size;
};

static struct dram_para_t dram_para_d1s_ddr2 = {
	0x00000210,
	0x00000002,
	0x007b7bf9,
	0x00000000,
	0x000000d2,
	0x00000000,
	0x00000e73,
	0x00000002,
	0x00000000,
	0x00000000,
	0x00471992,
	0x0131a10c,
	0x00057041,
	0xb4787896,
	0x00000000,
	0x48484848,
	0x00000048,
	0x1621121e,
	0x00000000,
	0x00000000,
	0x00000000,
	0x00030010,
	0x00000035,
	0x34000000,
};

static struct dram_para_t dram_para_ddr3 = {
	0x00000318,
	0x00000003,
	0x007b7bfb,
	0x00000001,
	0x000010d2,
	0x00000000,
	0x00001c70,
	0x00000042,
	0x00000018,
	0x00000000,
	0x004a2195,
	0x02423190,
	0x0008b061,
	0xb4787896,
	0x00000000,
	0x48484848,
	0x00000048,
	0x1620121e,
	0x00000000,
	0x00000000,
	0x00000000,
	0x00870000,
	0x00000024,
	0x34050100,
};

static int sunxi_ram_probe(struct udevice *dev)
{
	struct sunxi_ram_priv *priv = dev_get_priv(dev);
	struct dram_para_t *dram_para;
	int ret;

	if (of_machine_is_compatible("allwinner,sun20i-d1"))
		dram_para = &dram_para_ddr3;
	else
		dram_para = &dram_para_d1s_ddr2;

	ret = init_DRAM(0, dram_para);
	if (ret <= 0) {
		printf("DRAM init failed: %d\n", ret);
		return ret;
	}

	priv->size = ret * 1024 * 1024;

	return 0;
}

static int sunxi_ram_get_info(struct udevice *dev, struct ram_info *info)
{
	struct sunxi_ram_priv *priv = dev_get_priv(dev);

	info->base = CONFIG_SYS_SDRAM_BASE;
	info->size = priv->size;

	return 0;
}

static struct ram_ops sunxi_ram_ops = {
	.get_info = sunxi_ram_get_info,
};

static const struct udevice_id sunxi_ram_ids[] = {
	{ .compatible = "allwinner,sun20i-d1-mbus" },
	{ }
};

U_BOOT_DRIVER(sunxi_ram) = {
	.name		= "sunxi_ram",
	.id		= UCLASS_RAM,
	.of_match	= sunxi_ram_ids,
	.probe		= sunxi_ram_probe,
	.priv_auto	= sizeof(struct sunxi_ram_priv),
	.ops		= &sunxi_ram_ops,
};

#endif
