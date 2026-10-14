'use strict';
'require view';
'require form';
'require fs';
'require ui';

return view.extend({
	render: function() {
		var m, s, o;

		m = new form.Map('dailyreport', _('System & Traffic Daily Report'),
			_('Automatically collects system health, WAN traffic, top sites, and security audit, delivering scheduled reports.'));

		// 1. General Settings
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('General Settings'));

		o = s.option(form.Flag, 'enabled', _('Enable Daily Report Service'));
		o.rmempty = false;

		o = s.option(form.ListValue, 'send_hour', _('Daily Delivery Hour (O\'clock)'));
		for (var i = 0; i < 24; i++) {
			var h = (i < 10 ? '0' : '') + i;
			o.value(h, h + ':00');
		}

		o = s.option(form.Value, 'alert_temp', _('CPU High Temp Alert Threshold (°C)'));
		o.datatype = 'uinteger';
		o.placeholder = '75';

		o = s.option(form.Flag, 'auto_poweroff', _('Enable Emergency Overheat Shutdown'));
		o.description = _('Sends urgent critical Gotify/Email alert and safely powers off after 3 minutes if CPU exceeds threshold.');
		o.default = '0';

		// Send Test Report Button
		o = s.option(form.Button, '_send_now', _('Send Test Report'));
		o.inputtitle = _('Generate & Send Report Now');
		o.inputstyle = 'apply';
		o.onclick = function() {
			ui.showModal(_('Generating and Sending Report…'), [
				E('p', { 'class': 'spinning' }, _('Collecting system status and traffic data, sending to configured channels, please wait…'))
			]);
			return fs.exec_direct('/usr/share/dailyreport/dailyreport_core.sh', ['test']).then(function(out) {
				ui.hideModal();
				ui.showModal(_('Test Execution Result'), [
					E('p', _('Execution Output Log:')),
					E('pre', { 'style': 'max-height:300px;overflow-y:auto;background:#222;color:#0f0;padding:10px;font-size:12px;' }, out || _('(Finished)')),
					E('div', { 'class': 'right', 'style': 'margin-top:15px;' }, [
						E('button', {
							'class': 'btn cbi-button-neutral',
							'click': ui.hideModal
						}, _('OK'))
					])
				]);
			}).catch(function(err) {
				ui.hideModal();
				ui.addNotification(null, E('p', _('Execution failed: %s').format(err.message)), 'danger');
			});
		};

		// 2. Feature Modules
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('Feature Modules Switch'));

		o = s.option(form.Flag, 'enable_traffic', _('LAN Devices WAN Traffic Ranking'));
		o = s.option(form.Flag, 'enable_sites', _('Top 15 Visited Websites Traffic'));
		o = s.option(form.Flag, 'enable_login_audit', _('SSH Login Audit (Incremental)'));
		o = s.option(form.Flag, 'enable_banip', _('banIP Security Audit (Auto-skipped if not installed)'));

		// 3. Email Settings
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('Email Notification (SMTP)'));

		o = s.option(form.Flag, 'email_enabled', _('Enable Email Notification'));

		o = s.option(form.Value, 'smtp_server', _('SMTP Server'));
		o.depends('email_enabled', '1');
		o.placeholder = 'smtp.example.com';

		o = s.option(form.Value, 'smtp_port', _('SMTP Port'));
		o.depends('email_enabled', '1');
		o.datatype = 'port';
		o.placeholder = '465';

		o = s.option(form.Flag, 'smtp_ssl', _('Enable SSL/TLS'));
		o.depends('email_enabled', '1');
		o.default = '1';

		o = s.option(form.Value, 'smtp_user', _('Username / Sender Account'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'smtp_pass', _('Password / Auth Token'));
		o.depends('email_enabled', '1');
		o.password = true;
		o.placeholder = '••••••••';

		o = s.option(form.Value, 'mail_from', _('Sender Address (From)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'router@example.com';

		o = s.option(form.Value, 'mail_to', _('Recipient Address (To)'));
		o.depends('email_enabled', '1');
		o.placeholder = 'your_name@example.com';

		// 4. Gotify Settings
		s = m.section(form.NamedSection, 'config', 'dailyreport', _('Gotify Notification (Optional)'));

		o = s.option(form.Flag, 'gotify_enabled', _('Enable Gotify Notification'));

		o = s.option(form.Value, 'gotify_url', _('Gotify Server URL'));
		o.depends('gotify_enabled', '1');
		o.placeholder = 'https://gotify.example.com';

		o = s.option(form.Value, 'gotify_token', _('Gotify App Token'));
		o.depends('gotify_enabled', '1');
		o.password = true;

		return m.render();
	}
});
