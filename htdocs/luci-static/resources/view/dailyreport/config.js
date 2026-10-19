'use strict';
'require view';
'require form';
'require fs';
'require ui';
'require uci';

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('dailyreport')
		]);
	},
	handleSaveApply: function(ev, mode) {
		return this.handleSave(ev).then(function() {
			window.location.reload();
		});
	},
	render: function(data) {
		var m, s, o;

		// 1. 动态读取当前配置的语言 (从官方 uci 对象中直接获取，彻底可靠)
		var currentLang = uci.get('dailyreport', 'config', 'lang') || 'zh_cn';
		var isZh = (currentLang === 'zh_cn');

		var tr = function(zh, en) {
			return isZh ? zh : en;
		};

		m = new form.Map('dailyreport', tr('系统与流量日报', 'System & Traffic Daily Report'),
			tr('每日定时汇总系统状态、全网外网流量、网站访问与安全审计，并自动推送报告。', 'Automatically collects system health, WAN traffic, top sites, and security audit, delivering scheduled reports.'));

		// 1. 基本设置 / General Settings
		s = m.section(form.NamedSection, 'config', 'dailyreport', tr('基本设置', 'General Settings'));

		o = s.option(form.ListValue, 'lang', tr('插件与报表语言 / Language', 'Language / 语言'));
		o.value('zh_cn', '简体中文 (Simplified Chinese)');
		o.value('en', 'English (英文)');
		o.default = 'zh_cn';
		o.rmempty = false;
		o.description = tr('修改语言后，点击底部的【保存并应用】，页面将自动切换语言。', 'After changing language, click [Save & Apply] at the bottom to reload in English.');

		o = s.option(form.Flag, 'enabled', tr('启用日报服务', 'Enable Daily Report Service'));
		o.rmempty = false;

		o = s.option(form.ListValue, 'send_hour', tr('每日推送时间 (整点)', 'Daily Delivery Hour (O\'clock)'));
		for (var i = 0; i < 24; i++) {
			var h = (i < 10 ? '0' : '') + i;
			o.value(h, h + ':00');
		}

		o = s.option(form.Value, 'alert_temp', tr('CPU 高温告警阈值 (°C)', 'CPU High Temp Alert Threshold (°C)'));
		o.datatype = 'uinteger';
		o.placeholder = '75';

		o = s.option(form.Flag, 'auto_poweroff', tr('启用高温紧急关机保护', 'Enable Emergency Overheat Shutdown'));
		o.description = tr('当 CPU 温度达到告警阈值时，向 Gotify 发送最高优先级紧急告警，并在 3 分钟后安全关机以保护硬件。', 'Sends urgent critical Gotify/Email alert and safely powers off after 3 minutes if CPU exceeds threshold.');
		o.default = '0';

		// 立即推送测试按钮
		o = s.option(form.Button, '_send_now', tr('立即推送测试', 'Send Test Report'));
		o.inputtitle = tr('立即生成并发送日报', 'Generate & Send Report Now');
		o.inputstyle = 'apply';
		o.onclick = function() {
			ui.showModal(tr('正在生成并发送日报…', 'Generating and Sending Report…'), [
				E('p', { 'class': 'spinning' }, tr('正在采集系统状态与流量数据，并调用推送通道，请稍候…', 'Collecting system status and traffic data, sending to configured channels, please wait…'))
			]);
			return fs.exec_direct('/usr/share/dailyreport/dailyreport_core.sh', ['test']).then(function(out) {
				ui.hideModal();
				ui.showModal(tr('推送测试结果', 'Test Execution Result'), [
					E('p', tr('执行输出日志：', 'Execution Output Log:')),
					E('pre', { 'style': 'max-height:300px;overflow-y:auto;background:#222;color:#0f0;padding:10px;font-size:12px;' }, out || tr('(执行完毕)', '(Finished)')),
					E('div', { 'class': 'right', 'style': 'margin-top:15px;' }, [
						E('button', {
							'class': 'btn cbi-button-neutral',
							'click': ui.hideModal
						}, tr('确定', 'OK'))
					])
				]);
			}).catch(function(err) {
				ui.hideModal();
				ui.addNotification(null, E('p', tr('执行失败: %s', 'Execution failed: %s').format(err.message)), 'danger');
			});
		};

		// 2. 功能模块
		s = m.section(form.NamedSection, 'config', 'dailyreport', tr('功能模块开关', 'Feature Modules Switch'));

		o = s.option(form.Flag, 'enable_traffic', tr('局域网各设备外网流量排行', 'LAN Devices WAN Traffic Ranking'));
		o = s.option(form.Flag, 'enable_sites', tr('网站访问流量 Top 15', 'Top 15 Visited Websites Traffic'));
		o = s.option(form.Flag, 'enable_login_audit', tr('SSH 登录审计 (增量)', 'SSH Login Audit (Incremental)'));
		o = s.option(form.Flag, 'enable_banip', tr('banIP 联动处决报告 (若未安装则自动跳过)', 'banIP Security Audit (Auto-skipped if not installed)'));

		// 3. 邮件设置
		s = m.section(form.NamedSection, 'config', 'dailyreport', tr('邮件推送通道 (SMTP)', 'Email Notification (SMTP)'));

		o = s.option(form.Flag, 'email_enabled', tr('启用邮件推送', 'Enable Email Notification'));

		o = s.option(form.Value, 'smtp_server', tr('SMTP 服务器', 'SMTP Server'));
		o.depends('email_enabled', '1');
		o.placeholder = 'smtp.example.com';

		o = s.option(form.Value, 'smtp_port', tr('SMTP 端口', 'SMTP Port'));
		o.depends('email_enabled', '1');
		o.datatype = 'port';
		o.placeholder = '465';

		o = s.option(form.Flag, 'smtp_ssl', tr('启用 SSL/TLS', 'Enable SSL/TLS'));
		o.depends('email_enabled', '1');
		o.default = '1';

		o = s.option(form.Value, 'smtp_user', tr('发件人账号 / 用户名', 'Username / Sender Account'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'smtp_pass', tr('发件人密码 / 授权码', 'Password / Auth Token'));
		o.depends('email_enabled', '1');
		o.password = true;
		o.placeholder = '••••••••';

		o = s.option(form.Value, 'mail_from', tr('发件人邮箱 (From)', 'Sender Address (From)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'mail_to', tr('收件人邮箱 (To)', 'Recipient Address (To)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'your_name@example.com';

		// 4. Gotify 设置
		s = m.section(form.NamedSection, 'config', 'dailyreport', tr('Gotify 推送通道 (可选)', 'Gotify Notification (Optional)'));

		o = s.option(form.Flag, 'gotify_enabled', tr('启用 Gotify 推送', 'Enable Gotify Notification'));

		o = s.option(form.Value, 'gotify_url', tr('Gotify 服务器地址', 'Gotify Server URL'));
		o.depends('gotify_enabled', '1');
		o.placeholder = 'https://gotify.example.com';

		o = s.option(form.Value, 'gotify_token', tr('Gotify 应用 Token', 'Gotify App Token'));
		o.depends('gotify_enabled', '1');
		o.password = true;

		return m.render();
	}
});
