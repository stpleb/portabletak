package gov.takx.api.plugin.app;
public abstract class AAppPlugin { protected IAppPluginDelegate delegate; protected AAppPlugin() { }
  public abstract void start() throws Exception; public abstract void stop() throws Exception; public abstract void shutdown() throws Exception; }
