import { expect, test } from '@playwright/test'

test('智能开荒位于智能调度末尾，显示前置条件并持久化独立开关', async ({page}, testInfo) => {
  await page.goto('/#/i/testpilot/task/OpsiScheduling')
  const group = page.locator('#group-OpsiSmartExplore')
  await expect(group.getByRole('heading', {name: '智能开荒', exact: true})).toBeVisible()
  await group.scrollIntoViewIfNeeded()
  const enable = page.locator('[id="OpsiScheduling.OpsiSmartExplore.Enable"]')
  const purchase = page.locator('[id="OpsiScheduling.OpsiScheduling.BuyActionPoint"]')
  const cleanup = page.locator('[id="OpsiScheduling.OpsiSmartExplore.EventCleanup"]')
  for (const control of [enable, purchase, cleanup]) await expect(control).toHaveAttribute('aria-checked', 'false')
  await expect(group).toContainText('仅默认黄币')
  await expect(group).toContainText('1360')
  await expect(group).toContainText('不能同时启用')
  await expect(page.locator('#group-OpsiScheduling .field-row').first()).toContainText('行动力不足时购买港口行动力')
  await expect(page.locator('#group-OpsiScheduling')).toContainText('每月仅一次')
  await expect(page.locator('.config-group').last()).toHaveAttribute('id', 'group-OpsiSmartExplore')
  const progress = page.locator('[id="OpsiScheduling.OpsiSmartExplore.Progress"]')
  await expect(progress).toBeDisabled()
  // 购买开关可以独立启用，智能开荒仍保持关闭。
  await purchase.click()
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
  await page.reload()
  await expect(purchase).toHaveAttribute('aria-checked', 'true')
  await expect(enable).toHaveAttribute('aria-checked', 'false')
  for (const control of [enable, cleanup]) {
    await control.click()
    await expect(control).toHaveAttribute('aria-checked', 'true')
  }
  // 刷新并读取服务端状态，确认不是仅在浏览器内切换。
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('azurpilot.edits.config:testpilot'))).toBeNull()
  await page.reload()
  for (const control of [enable, purchase, cleanup]) await expect(control).toHaveAttribute('aria-checked', 'true')
  await page.locator('#group-OpsiScheduling').screenshot({path: testInfo.outputPath('action-point-purchase-settings.png')})
  await group.scrollIntoViewIfNeeded()
  await group.screenshot({path: testInfo.outputPath('smart-explore-settings.png')})
})
