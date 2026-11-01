package gov.tak.api.comms.takserver;
public interface ITakServerConnectionManager { java.util.Collection<? extends ITakServerConnection> getTakServerConnections(boolean connectedOnly); }
