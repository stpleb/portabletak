package gov.tak.api.comms.takserver;
public interface ITakServerConnection { java.util.UUID getId(); String getDisplayName(); java.net.URI getUri(); boolean isConnected(); gov.tak.api.comms.commo.StreamingConnectionConfiguration getConnectionConfig(); }
