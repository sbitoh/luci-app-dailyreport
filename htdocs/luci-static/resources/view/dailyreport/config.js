'use strict';
'require view';
'require form';
'require fs';
'require ui';

return view.extend({
	render: function() {
		var m, s, o;

		m = new form.Map('dailyreport', _('系统与流量日报'),
			_('每日定时汇总系统状态、全网外网流量、网站访问与安全审计，并自动推送报告。'));

		// 1. 基本设置
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('基本设置'));

		o = s.option(form.Flag, 'enabled', _('启用日报服务'));
		o.rmempty = false;

		o = s.option(form.ListValue, 'send_hour', _('每日推送时间 (整点)'));
		for (var i = 0; i < 24; i++) {
			var h = (i < 10 ? '0' : '') + i;
			o.value(h, h + ':00');
		}

		o = s.option(form.Value, 'alert_temp', _('CPU 高温告警阈值 (°C)'));
		o.datatype = 'uinteger';
		o.placeholder = '75';

		o = s.option(form.Flag, 'auto_poweroff', _('启用高温紧急关机保护'));
		o.description = _('当 CPU 温度达到告警阈值时，向 Gotify 发送最高优先级紧急告警，并在 3 分钟后安全关机以保护硬件。');
		o.default = '0';

		// 立即推送测试按钮
		o = s.option(form.Button, '_send_now', _('立即推送测试'));
		o.inputtitle = _('立即生成并发送日报');
		o.inputstyle = 'apply';
		o.onclick = function() {
			ui.showModal(_('正在生成并发送日报…'), [
				E('p', { 'class': 'spinning' }, _('正在采集系统状态与流量数据，并调用推送通道，请稍候…'))
			]);
			return fs.exec_direct('/usr/share/dailyreport/dailyreport_core.sh', ['test']).then(function(out) {
				ui.hideModal();
				ui.showModal(_('推送测试结果'), [
					E('p', _('执行输出日志：')),
					E('pre', { 'style': 'max-height:300px;overflow-y:auto;background:#222;color:#0f0;padding:10px;font-size:12px;' }, out || _('(执行完毕)')),
					E('div', { 'class': 'right', 'style': 'margin-top:15px;' }, [
						E('button', {
							'class': 'btn cbi-button-neutral',
							'click': ui.hideModal
						}, _('确定'))
					])
				]);
			}).catch(function(err) {
				ui.hideModal();
				ui.addNotification(null, E('p', _('执行失败: %s').format(err.message)), 'danger');
			});
		};

		// 2. 功能模块
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('功能模块开关'));

		o = s.option(form.Flag, 'enable_traffic', _('局域网设备外网流量排行'));
		o = s.option(form.Flag, 'enable_sites', _('网站访问流量 Top 15'));
		o = s.option(form.Flag, 'enable_login_audit', _('SSH 登录审计 (增量)'));
		o = s.option(form.Flag, 'enable_banip', _('banIP 联动处决报告 (若未安装则自动跳过)'));

		// 3. 邮件推送设置
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('邮件推送通道 (SMTP / msmtp)'));

		o = s.option(form.Flag, 'email_enabled', _('启用邮件推送'));

		o = s.option(form.Value, 'smtp_server', _('SMTP 服务器'));
		o.depends('email_enabled', '1');
		o.placeholder = 'smtp.example.com';

		o = s.option(form.Value, 'smtp_port', _('SMTP 端口'));
		o.depends('email_enabled', '1');
		o.datatype = 'port';
		o.placeholder = '465';

		o = s.option(form.Flag, 'smtp_ssl', _('启用 SSL/TLS'));
		o.depends('email_enabled', '1');
		o.default = '1';

		o = s.option(form.Value, 'smtp_user', _('发件人账号 / 用户名'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'smtp_pass', _('发件人密码 / 授权码'));
		o.depends('email_enabled', '1');
		o.password = true;
		o.placeholder = '••••••••';

		o = s.option(form.Value, 'mail_from', _('发件人邮箱 (From)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'mail_to', _('收件人邮箱 (To)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'your_name@example.com';

		// 4. Gotify 推送通道
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('Gotify 推送通道 (可选)'));

		o = s.option(form.Flag, 'gotify_enabled', _('启用 Gotify 推送'));

		o = s.option(form.Value, 'gotify_url', _('Gotify 服务器地址'));
		o.depends('gotify_enabled', '1');
		o.placeholder = 'https://gotify.example.com';

		o = s.option(form.Value, 'gotify_token', _('Gotify 应用 Token'));
		o.depends('gotify_enabled', '1');
		o.password = true;

		return m.render();
	}
});
