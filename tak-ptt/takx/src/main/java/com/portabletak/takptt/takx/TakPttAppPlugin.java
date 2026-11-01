package com.portabletak.takptt.takx;

import gov.tak.api.comms.commo.StreamingConnectionConfiguration;
import gov.tak.api.comms.takserver.ITakServerConnection;
import gov.tak.api.comms.takserver.ITakServerConnectionManager;
import gov.takx.api.annotation.CallSign;
import gov.takx.api.annotation.InvokeOnEDT;
import gov.takx.api.event.takserver.TakServerConnectionOpened;
import gov.takx.api.plugin.annotations.AppPlugin;
import gov.takx.api.plugin.annotations.PluginDescriptor;
import gov.takx.api.plugin.annotations.PluginSpecification;
import gov.takx.api.plugin.app.AAppPlugin;
import gov.takx.api.plugin.app.IAppPluginManager;
import gov.takx.api.plugin.support.SupportedPlatformVersion;
import gov.takx.api.ui.ComponentPosition;
import gov.takx.platform.ui.ComponentDescriptorBuilder;
import jakarta.enterprise.event.Observes;
import jakarta.inject.Inject;

import java.net.URI;
import java.util.Collection;
import java.util.prefs.Preferences;

import javax.swing.SwingUtilities;

/**
 * TAK PTT inside TAKX: push-to-talk over Mumble with nothing to set up. It uses the TAK server TAKX
 * is connected to and signs in with the certificate TAKX enrolled there, as the ATAK plugin does.
 */
@AppPlugin(
        spec = @PluginSpecification(
                family = "takptt",
                type = "TakPttApp",
                version = "0.1.0",
                iconPath = "takptt.png",
                minimumSupportedVersion = SupportedPlatformVersion.V5_8
        ),
        descriptor = @PluginDescriptor(
                name = "TAK PTT",
                description = "Push to talk over your TAK server's voice server",
                longDescription = "Push-to-talk voice over Mumble. Signs in with your TAK certificate, "
                        + "so there are no extra accounts. Talks with TAK PTT on ATAK and other Mumble users."
        ),
        autoStartMode = IAppPluginManager.AutoStartMode.start
)
@SuppressWarnings("UnusedDeclaration")
public class TakPttAppPlugin extends AAppPlugin {
    @Inject
    private ITakServerConnectionManager servers;

    @Inject
    @CallSign
    private String callsign;

    private VoiceEngine engine;
    private TakPttPanel panel;
    private String activeServer;

    @Override
    public void start() {
        setupUI();
        useTakServer();
    }

    @InvokeOnEDT
    private void setupUI() {
        panel = new TakPttPanel();
        engine = new VoiceEngine(new Audio.Desktop(), Preferences.userNodeForPackage(TakPttAppPlugin.class),
                s -> SwingUtilities.invokeLater(() -> { if (panel != null) panel.show(s); }));
        panel.attach(engine);
        delegate.getUISpaceManager().addComponent(new ComponentDescriptorBuilder(panel, "TAK PTT")
                .setIcon(getClass().getClassLoader().getResource("takptt.png"))
                .setTooltipText("TAK PTT")
                .setComponentPosition(ComponentPosition.RIGHT)
                .build(), panel);
    }

    @Override
    public void stop() {
        engine.shutdown();
        engine = null;
        activeServer = null;
        teardownUI();
    }

    @InvokeOnEDT
    private void teardownUI() {
        delegate.getUISpaceManager().removeComponent(panel);
        panel = null;
    }

    @Override
    public void shutdown() {
    }

    /** A TAK connection came up: use it if voice has none yet. */
    private void onTakConnected(@Observes TakServerConnectionOpened event) {
        useTakServer();
    }

    /** The TAK server TAKX is connected to (or, when none is connected, the first one set up). */
    private synchronized void useTakServer() {
        if (engine == null || servers == null) return;
        Collection<? extends ITakServerConnection> list = servers.getTakServerConnections(true);
        if (list.isEmpty()) list = servers.getTakServerConnections(false);
        if (list.isEmpty()) {
            engine.problem("Connect TAKX to your PortableTAK server first");
            return;
        }
        ITakServerConnection c = list.iterator().next();
        String id = c.getId().toString();
        if (id.equals(activeServer)) return;
        StreamingConnectionConfiguration cfg = c.getConnectionConfig();
        URI uri = c.getUri();
        try {
            VoiceLogin login = VoiceLogin.create(c.getDisplayName(), uri.getHost(), VoiceLogin.PORT,
                    cfg.getCertificateBytes(), cfg.getCertificatePassword(),
                    cfg.getCertificateAuthorityBytes(), cfg.getCertificateAuthorityPassword());
            activeServer = id;
            engine.connect(login, callsign);
        } catch (VoiceLogin.SetupException e) {
            engine.problem(e.getMessage());
        }
    }
}
